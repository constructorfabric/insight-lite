#!/usr/bin/env python3
"""SQLite store for the reporting tool.

One database file (default `history/report.db`, override with `REPORT_DB`) holds
everything that benefits from durable, queryable storage:

  * traffic    — raw per-repo/per-day clones & views. GitHub's Traffic API only
                 returns the trailing 14 days, so these are UNRECOVERABLE once
                 they age out; accumulating them here preserves the full series.
  * snapshots  — one row per collector run (per day), for contribution trends.
  * commits / pull_request / issue — granular event rows; any report period is
                 a date-range query over these (see aggregate()).
  * person / repo — dimension snapshots (identity/company, classification).

(Raw GitHub API responses are cached as JSON files under .cache/, not here.)

The DB lives on the docker bind-mount (`.:/work`), so it persists across
container restarts without committing a binary blob to git. It is intentionally
git-ignored; the JSONL files under history/ remain as a portable export/seed.
"""
from __future__ import annotations

import json
import os
import sqlite3
import statistics
from datetime import datetime, date, timedelta, timezone

import metrics_registry as _mreg
import tokens          # GENERATED — see tools/gen_tokens.py, design/tokens.json
import paths
_m = _mreg.metric

# The repo root, one level up from backend/: templates/, assets/ and config.yaml
# live there, not next to the modules.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def db_path() -> str:
    return os.environ.get("REPORT_DB") or str(paths.data_path("history", "report.db"))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS traffic (
    repo          TEXT NOT NULL,
    date          TEXT NOT NULL,
    clones        INTEGER DEFAULT 0,
    clone_uniques INTEGER DEFAULT 0,
    views         INTEGER DEFAULT 0,
    view_uniques  INTEGER DEFAULT 0,
    PRIMARY KEY (repo, date)
);
CREATE TABLE IF NOT EXISTS snapshots (
    date          TEXT PRIMARY KEY,
    generated_at  TEXT,
    lookback_days INTEGER,
    totals        TEXT,       -- JSON
    by_company    TEXT        -- JSON
);
CREATE TABLE IF NOT EXISTS runs (
    date          TEXT PRIMARY KEY,   -- one full run per calendar day
    generated_at  TEXT,
    lookback_days INTEGER,
    org           TEXT,
    payload       TEXT                -- the complete data.json blob
);
CREATE TABLE IF NOT EXISTS person_runs (
    date                 TEXT NOT NULL,
    login                TEXT NOT NULL,
    name                 TEXT,
    company              TEXT,
    is_member            INTEGER,
    commits              INTEGER,
    meaningful_additions INTEGER,
    prs_opened           INTEGER,
    prs_merged           INTEGER,
    specs                INTEGER,
    bugs                 INTEGER,
    features             INTEGER,
    reviews_given        INTEGER,
    approvals_given      INTEGER,
    ai_commits           INTEGER,
    cpt_lines            INTEGER,
    surviving_code_human INTEGER,
    surviving_code_ai    INTEGER,
    surviving_spec_human INTEGER,
    median_ttm_h         REAL,
    total_activity       INTEGER,
    PRIMARY KEY (date, login)
);
CREATE INDEX IF NOT EXISTS idx_person_runs_login ON person_runs(login);
CREATE INDEX IF NOT EXISTS idx_person_runs_company ON person_runs(company);
CREATE TABLE IF NOT EXISTS repo_runs (
    date              TEXT NOT NULL,
    repo              TEXT NOT NULL,   -- "org/name"
    org               TEXT,
    name              TEXT,
    classification    TEXT,
    element           TEXT,
    legacy_only       INTEGER,
    archived          INTEGER,
    stars             INTEGER,
    forks             INTEGER,
    commits_window    INTEGER,
    ai_commits_window INTEGER,
    prs_opened_window INTEGER,
    prs_merged_window INTEGER,
    code_loc          INTEGER,
    spec_loc          INTEGER,
    total_loc         INTEGER,
    clones_14d        INTEGER,
    views_14d         INTEGER,
    traffic_access    INTEGER,
    PRIMARY KEY (date, repo)
);
CREATE INDEX IF NOT EXISTS idx_repo_runs_repo ON repo_runs(repo);

-- ---- granular event tables: any period = a date-range query over these -------
CREATE TABLE IF NOT EXISTS commits (
    repo                 TEXT NOT NULL,
    sha                  TEXT NOT NULL,
    committed_at         TEXT,           -- UTC ISO 'YYYY-MM-DDTHH:MM:SSZ'
    author_email         TEXT,
    author_login         TEXT,
    classification       TEXT,           -- platform | app
    additions            INTEGER DEFAULT 0,
    deletions            INTEGER DEFAULT 0,
    meaningful_additions INTEGER DEFAULT 0,
    meaningful_deletions INTEGER DEFAULT 0,
    is_spec              INTEGER DEFAULT 0,
    commit_type          TEXT,
    ai_marked            INTEGER DEFAULT 0,
    ai_loc               INTEGER DEFAULT 0,
    ai_tools             TEXT DEFAULT '', -- comma-joined tool names on ai_marked commits
    is_bot               INTEGER DEFAULT 0,
    title                TEXT DEFAULT '', -- commit subject (first line), for drill rows
    -- when this row first entered THIS db, NOT when the work happened. Nullable on
    -- purpose: NULL = "was already here before we started recording" (see connect()).
    first_seen           TEXT,
    PRIMARY KEY (repo, sha)
);
CREATE INDEX IF NOT EXISTS idx_commit_date ON commits(committed_at);
CREATE INDEX IF NOT EXISTS idx_commit_login ON commits(author_login);
CREATE TABLE IF NOT EXISTS pull_request (
    repo                TEXT NOT NULL,
    number              INTEGER NOT NULL,
    org                 TEXT,
    author_login        TEXT,
    created_at          TEXT,
    merged_at           TEXT,
    review_requested_at TEXT,
    classification      TEXT,
    is_migration        INTEGER DEFAULT 0,
    is_bot              INTEGER DEFAULT 0,
    state               TEXT DEFAULT '',    -- OPEN | CLOSED | MERGED
    closed_at           TEXT DEFAULT '',    -- set for closed-unmerged too
    additions           INTEGER,
    deletions           INTEGER,
    changed_files       INTEGER,
    review_count        INTEGER,
    comment_count       INTEGER,
    author_association  TEXT DEFAULT '',    -- MEMBER | CONTRIBUTOR | FIRST_TIME | …
    closes_issues       INTEGER,            -- count of linked "Closes #" issues
    is_revert           INTEGER DEFAULT 0,
    is_draft            INTEGER DEFAULT 0,
    labels              TEXT DEFAULT '',    -- JSON array of label names
    title               TEXT DEFAULT '',    -- PR title, for drill rows
    PRIMARY KEY (repo, number)
);
CREATE INDEX IF NOT EXISTS idx_pr_date ON pull_request(created_at);
CREATE INDEX IF NOT EXISTS idx_pr_login ON pull_request(author_login);
CREATE TABLE IF NOT EXISTS issue (
    repo          TEXT NOT NULL,
    number        INTEGER NOT NULL,
    org           TEXT,
    author_login  TEXT,
    created_at    TEXT,
    is_bug        INTEGER DEFAULT 0,   -- derived: semantic category == 'bug'
    is_feature    INTEGER DEFAULT 0,   -- derived: semantic category == 'feature'
    is_epic       INTEGER DEFAULT 0,   -- derived: semantic category == 'epic'
    is_migration  INTEGER DEFAULT 0,
    is_bot        INTEGER DEFAULT 0,
    issue_type    TEXT DEFAULT '',   -- native Issue Type (Bug/Feature/Task/…), raw
    labels        TEXT DEFAULT '',   -- JSON array of all label names, raw
    state         TEXT DEFAULT '',   -- OPEN | CLOSED
    state_reason  TEXT DEFAULT '',   -- COMPLETED | NOT_PLANNED | REOPENED
    closed_at     TEXT DEFAULT '',
    assignees     TEXT DEFAULT '',   -- JSON array of assignee logins
    milestone     TEXT DEFAULT '',
    title         TEXT DEFAULT '',   -- issue title, for drill rows
    PRIMARY KEY (repo, number)
);
CREATE INDEX IF NOT EXISTS idx_issue_date ON issue(created_at);
CREATE INDEX IF NOT EXISTS idx_issue_login ON issue(author_login);
CREATE TABLE IF NOT EXISTS review (
    repo           TEXT NOT NULL,
    pr_number      INTEGER,
    reviewer_login TEXT,
    state          TEXT,
    submitted_at   TEXT           -- UTC ISO
);
CREATE INDEX IF NOT EXISTS idx_review_date ON review(submitted_at);
CREATE INDEX IF NOT EXISTS idx_review_login ON review(reviewer_login);
-- ---- issue/PR timeline events (RETROSPECTIVE flow signal). Unlike Projects-v2
-- board Status (no change-history in the API), these lifecycle events DO carry
-- historical timestamps, so we can reconstruct forward/backward flow over the
-- whole history. event ∈ ready_for_review | convert_to_draft | review_requested |
-- reopened | merged | closed | assigned. --------------------------------------
CREATE TABLE IF NOT EXISTS timeline_event (
    repo        TEXT NOT NULL,
    item_type   TEXT,              -- issue | pull_request
    number      INTEGER,
    event       TEXT,
    actor_login TEXT,
    created_at  TEXT,              -- UTC ISO
    PRIMARY KEY (repo, number, event, created_at)
);
CREATE INDEX IF NOT EXISTS idx_tl_item ON timeline_event(repo, number);
CREATE INDEX IF NOT EXISTS idx_tl_event ON timeline_event(event);
-- ---- ground-truth labels for the developer score: manager/peer ratings on a
-- sample, to CALIBRATE (validate) the score against a real signal. One rating per
-- (rater, subject); re-rating updates. Kept separate from the score itself. -----
CREATE TABLE IF NOT EXISTS score_label (
    subject_login TEXT NOT NULL,
    rater         TEXT NOT NULL,
    rating        INTEGER,          -- 1..5
    note          TEXT DEFAULT '',
    created_at    TEXT,
    PRIMARY KEY (subject_login, rater)
);
CREATE INDEX IF NOT EXISTS idx_label_subject ON score_label(subject_login);
-- ---- Projects v2 status snapshots (forward-only; the API has no field-change
-- history, so we record each item's current status per run and derive transitions
-- & cycle-time going forward). status_raw is the RAW board value; stage
-- normalization is a semantic-config derivation, not stored here. -------------
CREATE TABLE IF NOT EXISTS work_item_status (
    taken_at   TEXT NOT NULL,     -- snapshot instant, UTC ISO (several per day allowed)
    date       TEXT NOT NULL,     -- taken_at[:10], YYYY-MM-DD, for daily grouping
    item_id    TEXT NOT NULL,     -- stable ProjectV2Item node id
    project    TEXT,              -- "org/number"
    item_type  TEXT,              -- issue | pull_request | draft
    repo       TEXT,              -- "org/name" (null for draft items)
    number     INTEGER,           -- issue/PR number (null for draft)
    status_raw TEXT,              -- the board's Status field value, verbatim
    title      TEXT,
    updated_at TEXT,              -- ProjectV2Item.updatedAt: when the item last changed
                                  -- (≈ last status move; sharpens dwell / aging)
    PRIMARY KEY (taken_at, item_id)
);
-- (item_id, date), not item_id alone: flow_metrics resolves the LATEST snapshot per
-- item by joining this table to a MAX(date)-per-item subquery. With only item_id
-- indexed, SQLite drove that join off idx_wis_date — and `date` is not selective here,
-- because 141k snapshot rows over 1.6k items means each date=? lookup reads a great
-- many rows to keep a few. The pair makes the subquery a covering scan and the join a
-- point lookup: 1.663s -> 0.009s measured on the Constructor org, +6.5MB of index.
-- It also covers every item_id-only lookup as a prefix, so it REPLACES idx_wis_item
-- rather than joining it (see the migration, which drops the old one).
CREATE INDEX IF NOT EXISTS idx_wis_item_date ON work_item_status(item_id, date);
CREATE INDEX IF NOT EXISTS idx_wis_repo ON work_item_status(repo, number);
CREATE INDEX IF NOT EXISTS idx_wis_date ON work_item_status(date);
-- Derived from work_item_status, never a substitute for it. The snapshot table is a
-- daily photograph of every tracked item, so nearly all of it is the same status written
-- again: on the Constructor org 1,816 rows of 141,141 carry a change, and the two metrics
-- that walk item sequences were reading all 141,141 to find them.
--
-- NOTHING IS DROPPED. Every snapshot ever taken stays in work_item_status; these two
-- tables are a cache of the subset those readers need, rebuilt from it by
-- store.refresh_work_item_key() and safe to delete at any time.
--
-- `work_item_key` holds each status CHANGE plus each item's LAST sighting. The last
-- sighting is the half that is easy to miss: stage_dwell's "waiting now" lens counts
-- items whose newest row IS the latest snapshot, which is precisely the items that have
-- NOT changed recently — change rows alone would report almost nobody as waiting, and
-- report it calmly.
--
-- `work_item_instant` holds the distinct snapshot instants, because "which days did we
-- capture" and "what is the latest instant" are properties of the snapshot SET and
-- cannot be derived from a row list that deliberately omits rows.
--
-- prev_status_raw carries the status the item held in its previous snapshot, which the
-- rebuild computes anyway (a LAG window) — so the table doubles as the board's TRANSITION
-- log, readable straight from SQL, and every row says which of the three kinds it is:
--   prev_status_raw IS NULL          the item's first sighting
--   prev_status_raw <> status_raw    a transition, prev -> status_raw
--   prev_status_raw =  status_raw    a last sighting only, nothing moved
CREATE TABLE IF NOT EXISTS work_item_key (
    taken_at        TEXT NOT NULL,
    updated_at      TEXT,
    item_id         TEXT NOT NULL,
    repo            TEXT,
    number          INTEGER,
    item_type       TEXT,
    status_raw      TEXT,
    title           TEXT,
    -- last, so a database migrated with ALTER TABLE has the same layout as a fresh one
    prev_status_raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_wik_item ON work_item_key(item_id, taken_at);
CREATE TABLE IF NOT EXISTS work_item_instant (taken_at TEXT PRIMARY KEY);
-- ---- Projects v2 OTHER board fields (Priority, Iteration/Sprint, Estimate, custom
-- single-select/number/text/date). Same no-history limitation as Status, so snapshot
-- them alongside it. EAV so any field set works without a migration per new field. ---
CREATE TABLE IF NOT EXISTS work_item_field (
    taken_at TEXT NOT NULL,      -- snapshot instant (matches work_item_status.taken_at)
    date     TEXT NOT NULL,      -- taken_at[:10]
    item_id  TEXT NOT NULL,      -- ProjectV2Item node id
    project  TEXT, repo TEXT, number INTEGER,
    field    TEXT NOT NULL,      -- board field name, verbatim (e.g. "Priority")
    value    TEXT,               -- the value as text (single-select name / number / iteration title / date)
    PRIMARY KEY (taken_at, item_id, field)
);
CREATE INDEX IF NOT EXISTS idx_wif_field ON work_item_field(field);
CREATE INDEX IF NOT EXISTS idx_wif_item ON work_item_field(item_id);
CREATE INDEX IF NOT EXISTS idx_wif_date ON work_item_field(date);
-- ---- repo metadata snapshots (daily). stars/forks/archived/etc. carry no history
-- in the API, so we record a dated row to reconstruct trends going forward. ---------
CREATE TABLE IF NOT EXISTS repo_snapshot (
    date TEXT NOT NULL, repo TEXT NOT NULL,
    stars INTEGER, forks INTEGER, archived INTEGER DEFAULT 0,
    element TEXT, classification TEXT, code_loc INTEGER, spec_loc INTEGER,
    PRIMARY KEY (date, repo)
);
CREATE INDEX IF NOT EXISTS idx_reposnap_repo ON repo_snapshot(repo);
-- ---- org membership snapshots (daily). Who is a member + role — no history in the
-- API, so a dated roster lets us see joins / departures over time. -----------------
CREATE TABLE IF NOT EXISTS membership_snapshot (
    date TEXT NOT NULL, org TEXT NOT NULL, login TEXT NOT NULL,
    role TEXT DEFAULT 'member',
    PRIMARY KEY (date, org, login)
);
CREATE INDEX IF NOT EXISTS idx_memsnap_login ON membership_snapshot(login);
-- ---- CI runs (GitHub Actions) — RAW; workflow role (gate/nightly/…) and what
-- counts as a green run are semantic-config derivations, not stored here. -----
CREATE TABLE IF NOT EXISTS ci_run (
    repo           TEXT NOT NULL,
    run_id         INTEGER NOT NULL,
    workflow       TEXT,
    event          TEXT,           -- push | pull_request | schedule | …
    branch         TEXT,           -- head_branch
    status         TEXT,           -- queued | in_progress | completed
    conclusion     TEXT,           -- success | failure | skipped | cancelled | …
    created_at     TEXT,
    run_started_at TEXT,
    updated_at     TEXT,
    duration_s     INTEGER,        -- updated_at - run_started_at, completed runs only
    head_sha       TEXT,
    actor          TEXT,
    PRIMARY KEY (repo, run_id)
);
CREATE INDEX IF NOT EXISTS idx_ci_run_date ON ci_run(created_at);
CREATE INDEX IF NOT EXISTS idx_ci_run_repo ON ci_run(repo, workflow);
-- ---- dimension snapshots (current identity/company + repo classification) ----
CREATE TABLE IF NOT EXISTS person (
    login                TEXT PRIMARY KEY,
    name                 TEXT,
    company              TEXT,
    is_member            INTEGER,
    emails               TEXT,           -- JSON
    surviving_code_human INTEGER DEFAULT 0,
    surviving_code_ai    INTEGER DEFAULT 0,
    surviving_spec       INTEGER DEFAULT 0,
    cpt_lines            INTEGER DEFAULT 0,
    reviews_given        INTEGER DEFAULT 0,
    approvals_given      INTEGER DEFAULT 0,
    median_ttm_h         REAL,
    identity_confidence  TEXT,
    identity_evidence    TEXT,           -- JSON
    gh_name              TEXT DEFAULT '', -- GitHub profile (resolution hint, as
    gh_company           TEXT DEFAULT '', -- the person states it — free text, not
    gh_bio               TEXT DEFAULT '', -- our canonical company)
    gh_location          TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS repo (
    key            TEXT PRIMARY KEY,     -- "org/name"
    org            TEXT,
    name           TEXT,
    classification TEXT,
    element        TEXT,
    legacy_only    INTEGER DEFAULT 0,
    archived       INTEGER DEFAULT 0,
    stars          INTEGER DEFAULT 0,
    forks          INTEGER DEFAULT 0,
    code_loc       INTEGER,
    spec_loc       INTEGER
);
-- ---- human edits (the ONLY source of truth for identity + config overrides) --
-- Portal edits land here (atomic SQLite). There is no YAML mirror any more: the
-- backups were regenerated from this table, so they held nothing this table did
-- not, and the read path that imported them fed a test fixture into prod.
-- Recovery is a report.db snapshot (history/backups/, written before deploys).
-- scope: person | company_domain | repo | extra_org | extra_repo | element_extra
-- value: JSON object (e.g. person -> {company,name,aliases,is_bot})
CREATE TABLE IF NOT EXISTS override (
    scope      TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL DEFAULT '{}',   -- JSON
    updated_at TEXT,
    PRIMARY KEY (scope, key)
);
-- ---- secrets (GitHub token, …) --------------------------------------------
-- Server-side only. NEVER rendered to a client, NEVER exported to YAML or git.
-- Kept apart from `override` precisely so it can't leak into the backup files.
CREATE TABLE IF NOT EXISTS secret (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT
);
-- ---- usage analytics of the report itself ---------------------------------
-- Meta-analytics: who (as a persona) opens the report and which widgets they
-- view. `page` events are logged server-side on the /report GET (JS-independent,
-- the authoritative adoption number); `tab`/`panel` events arrive via the
-- /api/usage sendBeacon collector. Identity is ALWAYS resolved server-side from
-- the oauth2-proxy headers — never trusted from the client payload. `ts` is
-- server-stamped ISO8601 UTC ending 'Z' (lexicographic compare == chronological).
CREATE TABLE IF NOT EXISTS usage_event (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL,   -- ISO8601 UTC ending 'Z', server-stamped
    session_id   TEXT,               -- client UUID per page load (tab/panel only)
    viewer_login TEXT,               -- resolved persona login; NULL if unresolved
    viewer_ident TEXT,               -- raw proxy identity, or 'anon' when unresolved
    kind         TEXT    NOT NULL,   -- 'page'|'tab'|'panel'|'drill'|'chat_open'|'chat_msg'
    target       TEXT,               -- tab mode / panel slug / chat view; NULL for 'page'/'chat_open'
    tab          TEXT,               -- active tab context for a panel event
    dwell_ms     INTEGER,            -- panel dwell before flush (optional)
    period       TEXT,               -- active period preset when fired (optional)
    tokens_in    INTEGER,            -- chat_msg: prompt tokens (server-recorded)
    tokens_out   INTEGER,            -- chat_msg: candidates+thoughts tokens
    tokens_cached INTEGER,           -- chat_msg: input tokens served from context cache
    cost_usd     REAL                -- chat_msg: cost if pricing configured, else NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_ts     ON usage_event(ts);
CREATE INDEX IF NOT EXISTS idx_usage_viewer ON usage_event(viewer_login);
CREATE INDEX IF NOT EXISTS idx_usage_kind   ON usage_event(kind, target);

-- Metrics-assistant conversation transcript: one row per message, grouped by
-- session_id. `text` is the CLEAN content (user's typed question or the assistant's
-- answer) — the server-added context/identity annotations are NOT stored. Identity is
-- resolved server-side. Optional retention via CHAT_HISTORY_DAYS (see prune_chat_messages).
CREATE TABLE IF NOT EXISTS chat_message (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,   -- ISO8601 UTC, server-stamped
    session_id    TEXT,               -- conversation id (one per panel session)
    viewer_login  TEXT,               -- resolved persona login; NULL if unresolved
    viewer_ident  TEXT,               -- raw proxy identity, or 'anon'
    role          TEXT    NOT NULL,   -- 'user' | 'assistant'
    text          TEXT    NOT NULL,   -- clean message content
    view          TEXT,               -- report view when asked (user rows)
    period        TEXT,
    tokens_in     INTEGER,            -- assistant rows: the turn's usage
    tokens_out    INTEGER,
    tokens_cached INTEGER,
    cost_usd      REAL
);
CREATE INDEX IF NOT EXISTS idx_chatmsg_ts      ON chat_message(ts);
CREATE INDEX IF NOT EXISTS idx_chatmsg_session ON chat_message(session_id, id);
CREATE INDEX IF NOT EXISTS idx_chatmsg_viewer  ON chat_message(viewer_login);

-- Tool calls the assistant made per turn: which read-only tool, its arguments (for
-- sql_query, the SQL itself — a signal for which raw queries deserve a dedicated
-- tool), and a truncated result. Linked to the assistant chat_message via message_id.
CREATE TABLE IF NOT EXISTS chat_tool_call (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    session_id    TEXT,
    viewer_login  TEXT,
    viewer_ident  TEXT,
    message_id    INTEGER,            -- assistant chat_message.id for the turn
    seq           INTEGER,            -- call order within the turn
    tool_name     TEXT    NOT NULL,
    args          TEXT,               -- JSON of the call arguments (truncated)
    result        TEXT,               -- JSON of the tool result (truncated)
    result_bytes  INTEGER,            -- full result size before truncation
    ok            INTEGER             -- 1 = succeeded, 0 = errored
);
CREATE INDEX IF NOT EXISTS idx_toolcall_ts    ON chat_tool_call(ts);
CREATE INDEX IF NOT EXISTS idx_toolcall_name  ON chat_tool_call(tool_name);
CREATE INDEX IF NOT EXISTS idx_toolcall_msg   ON chat_tool_call(message_id);

CREATE TABLE IF NOT EXISTS dashboard (
    id           TEXT    PRIMARY KEY,
    owner_login  TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    visibility   TEXT    NOT NULL DEFAULT 'private',
    spec         TEXT    NOT NULL,
    created_ts   TEXT    NOT NULL,
    updated_ts   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dashboard_owner ON dashboard(owner_login);
"""

# ---- company colours ------------------------------------------------------------
#
# A company's colour comes from its NAME, so it survives the thing that used to change
# it: rank. The previous rule handed out palette entries in descending-commit order, so
# the moment two companies swapped places they swapped colours, and a reader comparing
# this week's chart with last week's was comparing different things. (Before the names
# were de-hardcoded for open sourcing, three were pinned here by name and the rank
# fallback only ever caught strangers — which is why the flaw stayed invisible until the
# pins went away.)
#
# The hash is spelled out rather than using hash(): Python randomises str hashing per
# process unless PYTHONHASHSEED is set, so hash() would give a different colour on every
# restart — precisely the bug this removes. Same rule as the element and work-type
# colours elsewhere in the codebase, so all three families behave alike.
#
# "Other" is the catch-all bucket and keeps a deliberate grey; it is never generated.
OTHER_COMPANY_COLOR = tokens.VALUES["company-empty"]
# Eight, and deliberately not more: padding this list out to lower the collision odds
# meant adding a second red and a second blue, which buys a statistic at the cost of the
# thing a palette is for — telling two series apart at a glance. Eight distinguishable
# colours plus a one-click pin beats twelve where four are near-duplicates.
# The teal here was #0a7ea4, which sits 58 units from the blue in RGB — close enough
# that two adjacent series read as the same colour. #14b8a6 is 95 from its nearest
# neighbour; the palette's tightest pair is now the purple/magenta one at 89.
COMPANY_PALETTE = tokens.COMPANY_PALETTE


def _name_hash(s) -> int:
    h = 0
    for ch in str(s).strip().lower():
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h


def pinned_company_colors() -> dict:
    """Explicit `companies.colors: {name: "#rrggbb"}` from the config, or {}.

    Pins win over generated colours — that is what they are for, and the way to keep a
    colour a team already reads as "us". `companies` is a BASE_KEY, so a pin written in
    config.yaml is captured into the override table with the rest of the structure.

    A failure here loses PINS, not data: callers fall back to generated colours, which
    are themselves stable. Hence {} rather than raising — but the except is narrow on
    purpose, so a real bug in the config layer still surfaces where it belongs.
    """
    try:
        import ghclient
    except ImportError:                  # pragma: no cover - config layer unavailable
        return {}
    raw = ((ghclient.load_config() or {}).get("companies") or {}).get("colors") or {}
    return {str(k): str(v) for k, v in raw.items() if k and v}


def company_color_map(names, pinned: dict | None = None) -> dict:
    """{company: colour}. A name's colour depends on THAT NAME and nothing else.

    The first version of this resolved collisions by probing to the next free palette
    slot, which sounded better and was not: the outcome then depended on which OTHER
    companies were in the set. In practice the Config screen listed two companies the
    report did not draw, one of them took a slot, and a third company was pushed to a
    different colour — so the swatch in the editor disagreed with the chart it was meant
    to describe. A colour that depends on the company's neighbours is the same class of
    bug as one that depends on its rank.

    So: no probing. Two names CAN land on the same colour, and when that actually
    matters, a human pins one of them on Manage → Config — which is a deliberate,
    one-click answer to a rare event, rather than a rule that quietly repaints a third
    party to avoid it.
    """
    pins = dict(pinned if pinned is not None else pinned_company_colors())
    out: dict = {}
    for n in names:
        name = str(n)
        if name in pins:
            out[name] = pins[name]
        elif name == "Other":
            out[name] = OTHER_COMPANY_COLOR
        else:
            out[name] = COMPANY_PALETTE[_name_hash(name) % len(COMPANY_PALETTE)]
    return out


def company_color(name, pinned: dict | None = None) -> str:
    """Single name. Prefer company_color_map for a set — only that de-duplicates."""
    return company_color_map([name], pinned)[str(name)]

# column order for the granular writers (also INSERT order)
# first_seen is the one commit column collectors do not supply — write_commits owns it,
# because only the DB knows when a row first arrived here.
COMMIT_COLS = ["repo", "sha", "committed_at", "author_email", "author_login",
               "classification", "additions", "deletions", "meaningful_additions",
               "meaningful_deletions", "is_spec", "commit_type", "ai_marked",
               "ai_loc", "ai_tools", "is_bot", "title", "first_seen"]
PR_COLS = ["repo", "number", "org", "author_login", "created_at", "merged_at",
           "review_requested_at", "classification", "is_migration", "is_bot",
           "state", "closed_at", "additions", "deletions", "changed_files",
           "review_count", "comment_count", "author_association", "closes_issues",
           "is_revert", "is_draft", "labels", "title"]
ISSUE_COLS = ["repo", "number", "org", "author_login", "created_at",
              "is_bug", "is_feature", "is_migration", "is_bot",
              "issue_type", "labels", "state", "state_reason", "closed_at",
              "assignees", "milestone", "title"]
CI_RUN_COLS = ["repo", "run_id", "workflow", "event", "branch", "status",
               "conclusion", "created_at", "run_started_at", "updated_at",
               "duration_s", "head_sha", "actor"]
DIM_PERSON_COLS = ["login", "name", "company", "is_member", "emails",
                   "surviving_code_human", "surviving_code_ai", "surviving_spec",
                   "cpt_lines", "reviews_given", "approvals_given", "median_ttm_h",
                   "identity_confidence", "identity_evidence",
                   "gh_name", "gh_company", "gh_bio", "gh_location"]
DIM_REPO_COLS = ["key", "org", "name", "classification", "element", "legacy_only",
                 "archived", "stars", "forks", "code_loc", "spec_loc"]

# Column order for the normalised per-run tables (also the INSERT order).
PERSON_COLS = [
    "name", "company", "is_member", "commits", "meaningful_additions",
    "prs_opened", "prs_merged", "specs", "bugs", "features",
    "reviews_given", "approvals_given", "ai_commits", "cpt_lines",
    "surviving_code_human", "surviving_code_ai", "surviving_spec_human",
    "median_ttm_h", "total_activity",
]
REPO_COLS = [
    "org", "name", "classification", "element", "legacy_only", "archived",
    "stars", "forks", "commits_window", "ai_commits_window",
    "prs_opened_window", "prs_merged_window", "code_loc", "spec_loc",
    "total_loc", "clones_14d", "views_14d", "traffic_access",
]
_BOOL_COLS = {"is_member", "legacy_only", "archived", "traffic_access"}


def connect() -> sqlite3.Connection:
    """Open (and lazily create) the database with sane pragmas."""
    path = db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    # wait up to 5s for a competing writer (portal save vs. a running collect)
    # instead of failing immediately with "database is locked"
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    # lightweight migration: ai_tools column added 2026-07 for the per-window
    # per-tool AI split; pre-existing DBs get it empty until the next collect run
    cols = {r[1] for r in conn.execute("PRAGMA table_info(commits)")}
    if "ai_tools" not in cols:
        conn.execute("ALTER TABLE commits ADD COLUMN ai_tools TEXT DEFAULT ''")
    # lightweight migration: GitHub-profile columns added 2026-07 as a resolution
    # hint (name/company/bio/location as the person states them on GitHub); empty
    # on pre-existing DBs until the next collect fetches profiles
    pcols = {r[1] for r in conn.execute("PRAGMA table_info(person)")}
    for col in ("gh_name", "gh_company", "gh_bio", "gh_location"):
        if col not in pcols:
            conn.execute(f"ALTER TABLE person ADD COLUMN {col} TEXT DEFAULT ''")
    # 2026-07: issue raw enrichment (native type, labels, lifecycle) — empty on
    # pre-existing rows until the next collect enriches them
    icols = {r[1] for r in conn.execute("PRAGMA table_info(issue)")}
    for col in ("issue_type", "labels", "state", "state_reason", "closed_at",
                "assignees", "milestone"):
        if col not in icols:
            conn.execute(f"ALTER TABLE issue ADD COLUMN {col} TEXT DEFAULT ''")
    # 2026-07: unified taxonomy — is_epic joins is_bug/is_feature as a derived
    # projection of semantic.categorize_issue (bug/story/epic). 0 on old rows until
    # the next collect / reconfig recomputes them.
    if "is_epic" not in icols:
        conn.execute("ALTER TABLE issue ADD COLUMN is_epic INTEGER DEFAULT 0")
    # 2026-07: rename the legacy is_user_story column to is_feature (it tracks the
    # 'feature' category; shown as "Features"). Data is preserved by the rename.
    if "is_feature" not in icols and "is_user_story" in icols:
        conn.execute("ALTER TABLE issue RENAME COLUMN is_user_story TO is_feature")
    # …and the matching rename on the per-run rollup (PERSON_COLS now writes
    # 'features'). Without this a pre-existing DB fails every collect with
    # "table person_runs has no column named features".
    prncols = {r[1] for r in conn.execute("PRAGMA table_info(person_runs)")}
    if "features" not in prncols and "user_stories" in prncols:
        conn.execute("ALTER TABLE person_runs RENAME COLUMN user_stories TO features")
    # 2026-07: PR raw enrichment (size, lifecycle, review/comment counts)
    prcols = {r[1] for r in conn.execute("PRAGMA table_info(pull_request)")}
    for col, typ in (("state", "TEXT DEFAULT ''"), ("closed_at", "TEXT DEFAULT ''"),
                     ("additions", "INTEGER"), ("deletions", "INTEGER"),
                     ("changed_files", "INTEGER"), ("review_count", "INTEGER"),
                     ("comment_count", "INTEGER"), ("author_association", "TEXT DEFAULT ''"),
                     ("closes_issues", "INTEGER"), ("is_revert", "INTEGER DEFAULT 0"),
                     ("is_draft", "INTEGER DEFAULT 0"), ("labels", "TEXT DEFAULT ''")):
        if col not in prcols:
            conn.execute(f"ALTER TABLE pull_request ADD COLUMN {col} {typ}")
    # 2026-07: title/subject stored for drill-down rows (commit subject, PR & issue
    # title) — empty on pre-existing rows until the next collect run backfills them
    if "title" not in cols:
        conn.execute("ALTER TABLE commits ADD COLUMN title TEXT DEFAULT ''")
    if "title" not in prcols:
        conn.execute("ALTER TABLE pull_request ADD COLUMN title TEXT DEFAULT ''")
    if "title" not in icols:
        conn.execute("ALTER TABLE issue ADD COLUMN title TEXT DEFAULT ''")
    # 2026-07: first_seen — the UTC instant a commit row first entered this DB. Every
    # windowed query filters on committed_at, which is the git AUTHOR date, so a PR that
    # sat open for 157 days and then merges on a merge- or rebase-strategy repo injects
    # its commits into windows five months back: "last 30 days" is not reproducible, and
    # the number someone screenshots today can differ tomorrow with no new work done.
    # first_seen makes "was in the window when the window closed" distinguishable from
    # "arrived later". Squash merges do NOT back-date (measured: 95% of squash commits
    # carry an author date within an hour of merged_at), so the exposure is per-repo and
    # very uneven — §1 of docs/superpowers/plans/2026-07-28-work-in-flight.md.
    # Pre-existing rows stay NULL rather than getting the migration's clock: their real
    # arrival is unknown, and stamping thousands of historical commits with "now" would
    # invent a zero-back-dating figure that looks like data. Anything reading this column
    # must treat NULL as "unknown / was already here", never as "new".
    if "first_seen" not in cols:
        conn.execute("ALTER TABLE commits ADD COLUMN first_seen TEXT")
    # 2026-07: board snapshots move from one-per-day (PK date,item_id) to timestamped
    # (PK taken_at,item_id) so intra-day status snapshots accumulate. Rebuild an old
    # table, mapping each existing daily row to a midnight-UTC taken_at.
    wcols = {r[1] for r in conn.execute("PRAGMA table_info(work_item_status)")}
    if wcols and "taken_at" not in wcols:
        conn.executescript("""
            ALTER TABLE work_item_status RENAME TO _wis_old;
            CREATE TABLE work_item_status (
                taken_at TEXT NOT NULL, date TEXT NOT NULL, item_id TEXT NOT NULL,
                project TEXT, item_type TEXT, repo TEXT, number INTEGER,
                status_raw TEXT, title TEXT, PRIMARY KEY (taken_at, item_id));
            INSERT OR IGNORE INTO work_item_status
                (taken_at, date, item_id, project, item_type, repo, number, status_raw, title)
                SELECT date || 'T00:00:00Z', date, item_id, project, item_type, repo,
                       number, status_raw, title FROM _wis_old;
            DROP TABLE _wis_old;
            CREATE INDEX IF NOT EXISTS idx_wis_item_date ON work_item_status(item_id, date);
            CREATE INDEX IF NOT EXISTS idx_wis_repo ON work_item_status(repo, number);
            CREATE INDEX IF NOT EXISTS idx_wis_date ON work_item_status(date);
        """)
    # 2026-07: item updatedAt captured per snapshot (sharpens stage dwell / aging).
    wcols2 = {r[1] for r in conn.execute("PRAGMA table_info(work_item_status)")}
    if wcols2 and "updated_at" not in wcols2:
        conn.execute("ALTER TABLE work_item_status ADD COLUMN updated_at TEXT")
    # 2026-07: idx_wis_item(item_id) is superseded by idx_wis_item_date(item_id, date),
    # which serves the same prefix lookups AND the latest-snapshot join flow_metrics
    # makes (see the index's comment in the schema above). The schema block created the
    # new one; drop the old rather than keep paying for it on every snapshot write.
    conn.execute("DROP INDEX IF EXISTS idx_wis_item")
    # 2026-08: prev_status_raw joined work_item_key, making it the board's transition log.
    # Emptying the cache is the whole migration — it is derived, and the next read of it
    # rebuilds it with the new column filled in (see refresh_work_item_key).
    kcols = {r[1] for r in conn.execute("PRAGMA table_info(work_item_key)")}
    if kcols and "prev_status_raw" not in kcols:
        conn.execute("ALTER TABLE work_item_key ADD COLUMN prev_status_raw TEXT")
        conn.execute("DELETE FROM work_item_key")
    # 2026-07: metrics-assistant token/cost accounting on chat_msg usage events.
    ucols = {r[1] for r in conn.execute("PRAGMA table_info(usage_event)")}
    for col, typ in (("tokens_in", "INTEGER"), ("tokens_out", "INTEGER"),
                     ("tokens_cached", "INTEGER"), ("cost_usd", "REAL")):
        if col not in ucols:
            conn.execute(f"ALTER TABLE usage_event ADD COLUMN {col} {typ}")
    return conn


# --- traffic ---------------------------------------------------------------
def upsert_traffic(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """Insert/refresh per-repo/per-day traffic rows. Latest value wins for a metric
    that was actually fetched; a NULL means "not fetched this run" and PRESERVES the
    stored value (COALESCE), so a failed views request can never zero out real
    history that GitHub only keeps for 14 days."""
    conn.executemany(
        """INSERT INTO traffic (repo, date, clones, clone_uniques, views, view_uniques)
           VALUES (:repo, :date, :clones, :clone_uniques, :views, :view_uniques)
           ON CONFLICT(repo, date) DO UPDATE SET
             clones=COALESCE(excluded.clones, traffic.clones),
             clone_uniques=COALESCE(excluded.clone_uniques, traffic.clone_uniques),
             views=COALESCE(excluded.views, traffic.views),
             view_uniques=COALESCE(excluded.view_uniques, traffic.view_uniques)""",
        rows,
    )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM traffic").fetchone()[0]


def read_traffic(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute("SELECT * FROM traffic ORDER BY date, repo")
    return [dict(r) for r in cur.fetchall()]


# --- snapshots -------------------------------------------------------------
def upsert_snapshot(conn: sqlite3.Connection, snap: dict) -> int:
    """Store one daily snapshot; a same-day re-run replaces that day's row."""
    conn.execute(
        """INSERT INTO snapshots (date, generated_at, lookback_days, totals, by_company)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(date) DO UPDATE SET
             generated_at=excluded.generated_at, lookback_days=excluded.lookback_days,
             totals=excluded.totals, by_company=excluded.by_company""",
        (snap["date"], snap.get("generated_at"), snap.get("lookback_days"),
         json.dumps(snap.get("totals", {})), json.dumps(snap.get("by_company", {}))),
    )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]


def refresh_work_item_key(conn: sqlite3.Connection, *, commit: bool = True) -> int:
    """Rebuild work_item_key / work_item_instant from work_item_status.

    Called by write_work_item_status, which is the table's ONLY writer — so there is no
    path that adds a snapshot without this running, and no cache that can drift behind
    the data. Rebuilds wholesale rather than incrementally: it costs ~0.6s on 141k rows,
    which is nothing inside a collection and is one fewer thing to get wrong than an
    incremental update that has to reason about a re-run overwriting one instant.

    `commit=False` leaves the transaction open so a caller can land the source write and
    this rebuild together. write_work_item_status needs that: committing separately would
    publish the new statuses while the cache still held the old ones, and under WAL a
    reader on another connection does not block, so it would see exactly that pair. The
    default stays True for the lazy build in semantic_metrics._board_key, which has no
    write of its own to bundle with.

    Reading from the result is 0.001s against 0.052s for the source, which takes
    /api/report/flow from 413ms to 116ms and the rewinds drill-down from 250ms to 15ms —
    measured on a copy of production, with byte-identical responses either way.

    A change row is the first sighting of an item or a status different from its previous
    snapshot; `rn = 1` is the last sighting. Built WITHOUT a repo filter and filtered on
    read: the one item in production whose repo changes mid-history makes the two differ
    by a single first-sighting row, and neither metric moves, because dwell skips the
    first observed run (its entry was never seen) and a rewind needs a predecessor."""
    conn.execute("DELETE FROM work_item_key")
    conn.execute(
        "INSERT INTO work_item_key (taken_at, updated_at, item_id, repo, number, "
        "item_type, status_raw, title, prev_status_raw) "
        "WITH s AS ("
        "  SELECT taken_at, updated_at, item_id, repo, number, item_type, status_raw,"
        "         title,"
        "         LAG(status_raw) OVER (PARTITION BY item_id ORDER BY taken_at) AS prev,"
        "         ROW_NUMBER() OVER (PARTITION BY item_id ORDER BY taken_at DESC) AS rn"
        "  FROM work_item_status WHERE status_raw IS NOT NULL"
        ") SELECT taken_at, updated_at, item_id, repo, number, item_type, status_raw,"
        "         title, prev"
        "  FROM s WHERE prev IS NULL OR prev <> status_raw OR rn = 1")
    conn.execute("DELETE FROM work_item_instant")
    conn.execute("INSERT INTO work_item_instant (taken_at) "
                 "SELECT DISTINCT taken_at FROM work_item_status "
                 "WHERE status_raw IS NOT NULL")
    if commit:
        conn.commit()
    return conn.execute("SELECT COUNT(*) FROM work_item_key").fetchone()[0]


def write_work_item_status(conn: sqlite3.Connection, taken_at: str, rows: list[dict]) -> int:
    """Record ONE Projects v2 status snapshot taken at `taken_at` (UTC ISO instant).
    Keyed by the exact instant, so several snapshots per day accumulate; a re-run with
    the same timestamp overwrites just that snapshot. `date` = taken_at[:10] for daily
    grouping. A plain YYYY-MM-DD is accepted too (becomes a midnight snapshot)."""
    date = taken_at[:10]
    conn.execute("DELETE FROM work_item_status WHERE taken_at=?", (taken_at,))
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO work_item_status "
            "(taken_at, date, item_id, project, item_type, repo, number, status_raw, title, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(taken_at, date, r["item_id"], r.get("project"), r.get("item_type"),
              r.get("repo"), r.get("number"), r.get("status_raw"), r.get("title"),
              r.get("updated_at")) for r in rows])
    # This is the table's only writer, so rebuilding the derived cache HERE is what makes
    # it impossible for the two to disagree — including on a re-run that overwrites one
    # instant in place, which changes statuses without changing the row count or the
    # newest timestamp and would defeat any cheap staleness marker.
    #
    # Both writes land in ONE transaction, which is what the previous sentence actually
    # requires: committing the statuses first and the cache second left a window where a
    # reader saw new statuses against the old cache, and _board_key's guard cannot catch
    # it — the guard rebuilds an EMPTY cache, and a stale one is merely behind. The window
    # is reachable, not theoretical: collection runs while the portal serves reads, which
    # is why connect() sets busy_timeout at all.
    refresh_work_item_key(conn, commit=False)
    conn.commit()
    return len(rows)


def read_work_item_status(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM work_item_status ORDER BY taken_at, item_id")]


def write_work_item_fields(conn: sqlite3.Connection, taken_at: str, rows: list[dict]) -> int:
    """One snapshot of Projects v2 board FIELD values (Priority, Iteration, …), keyed
    by the same taken_at instant as the Status snapshot. Idempotent per instant."""
    date = taken_at[:10]
    conn.execute("DELETE FROM work_item_field WHERE taken_at=?", (taken_at,))
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO work_item_field "
            "(taken_at, date, item_id, project, repo, number, field, value) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(taken_at, date, r["item_id"], r.get("project"), r.get("repo"),
              r.get("number"), r["field"], r.get("value")) for r in rows])
    conn.commit()
    return len(rows)


def write_repo_snapshot(conn: sqlite3.Connection, date: str, rows: list[dict]) -> int:
    """One day's repo-metadata snapshot (stars/forks/archived/…). Replaces just the
    given date so days accumulate."""
    conn.execute("DELETE FROM repo_snapshot WHERE date=?", (date,))
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO repo_snapshot "
            "(date, repo, stars, forks, archived, element, classification, code_loc, spec_loc) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [(date, r["repo"], r.get("stars"), r.get("forks"),
              1 if r.get("archived") else 0, r.get("element"), r.get("classification"),
              r.get("code_loc"), r.get("spec_loc")) for r in rows])
    conn.commit()
    return len(rows)


def write_membership_snapshot(conn: sqlite3.Connection, date: str, rows: list[dict]) -> int:
    """One day's org-membership snapshot (login + role per org). Replaces just the
    given date so joins/departures show up as the roster changes day to day."""
    conn.execute("DELETE FROM membership_snapshot WHERE date=?", (date,))
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO membership_snapshot (date, org, login, role) "
            "VALUES (?,?,?,?)",
            [(date, r["org"], r["login"], r.get("role", "member")) for r in rows])
    conn.commit()
    return len(rows)


def read_snapshots(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute("SELECT * FROM snapshots ORDER BY date")
    out = []
    for r in cur.fetchall():
        out.append({
            "date": r["date"], "generated_at": r["generated_at"],
            "lookback_days": r["lookback_days"],
            "totals": json.loads(r["totals"] or "{}"),
            "by_company": json.loads(r["by_company"] or "{}"),
        })
    return out


# --- full runs + normalised per-run people/repos --------------------------
def _row(cols: list[str], src: dict, key1, key2):
    """Build an INSERT tuple: (key1, key2, *cols) reading cols from src."""
    vals = [key1, key2]
    for c in cols:
        v = src.get(c)
        if c in _BOOL_COLS:
            v = int(bool(v))
        vals.append(v)
    return tuple(vals)


def upsert_run(conn: sqlite3.Connection, payload: dict) -> None:
    """Store one full run (the complete data.json blob) + normalised people/repos
    rows for cross-run SQL. Idempotent per day — a same-day re-run replaces it."""
    date = payload["generated_at"][:10]
    # backward-compat (mirrors render.build_model): a blob collected before the
    # user_stories→features rename has no 'features' key, so re-upserting it — which
    # reindex/reconfig do on every identity or config save — would write NULL over a
    # real feature count. Normalise first, so the stored blob ages out too.
    for p in (payload.get("people") or {}).values():
        if isinstance(p, dict) and "features" not in p:
            p["features"] = p.get("user_stories", 0)
    conn.execute(
        """INSERT INTO runs (date, generated_at, lookback_days, org, payload)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(date) DO UPDATE SET
             generated_at=excluded.generated_at, lookback_days=excluded.lookback_days,
             org=excluded.org, payload=excluded.payload""",
        (date, payload.get("generated_at"), payload.get("lookback_days"),
         payload.get("org"), json.dumps(payload)),
    )
    # normalised rows: delete the day's rows then re-insert (people/repos can drop out)
    conn.execute("DELETE FROM person_runs WHERE date=?", (date,))
    p_sql = ("INSERT INTO person_runs (date, login, " + ", ".join(PERSON_COLS) + ") "
             "VALUES (" + ", ".join(["?"] * (2 + len(PERSON_COLS))) + ")")
    conn.executemany(p_sql, [_row(PERSON_COLS, p, date, login)
                             for login, p in (payload.get("people") or {}).items()])
    conn.execute("DELETE FROM repo_runs WHERE date=?", (date,))
    r_sql = ("INSERT INTO repo_runs (date, repo, " + ", ".join(REPO_COLS) + ") "
             "VALUES (" + ", ".join(["?"] * (2 + len(REPO_COLS))) + ")")
    conn.executemany(r_sql, [_row(REPO_COLS, meta, date, key)
                             for key, meta in (payload.get("repos") or {}).items()])
    conn.commit()


def latest_run_meta(conn: sqlite3.Connection) -> dict | None:
    """Date + generated_at of the newest run WITHOUT deserialising the (multi-MB)
    payload — cheap enough for a freshness endpoint to be polled on a schedule."""
    row = conn.execute(
        "SELECT date, generated_at FROM runs ORDER BY date DESC LIMIT 1").fetchone()
    return {"date": row["date"], "generated_at": row["generated_at"]} if row else None


def read_latest_run(conn: sqlite3.Connection):
    """Return the most recent full run payload (the data.json equivalent), or None."""
    row = conn.execute(
        "SELECT payload FROM runs ORDER BY date DESC LIMIT 1").fetchone()
    return json.loads(row["payload"]) if row else None


def load_report_data(conn: sqlite3.Connection | None = None) -> dict | None:
    """Latest run payload with `_history` (snapshots) attached — what render and
    directory consume. Opens its own connection if none is given. None if empty."""
    own = conn is None
    if own:
        conn = connect()
    try:
        payload = read_latest_run(conn)
        if payload is None:
            return None
        payload["_history"] = read_snapshots(conn)
        return payload
    finally:
        if own:
            conn.close()


# --- granular event + dimension writers (full-replace per collect run) -----
def _coerce(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    return v


def _replace(conn: sqlite3.Connection, table: str, cols: list[str], rows: list[dict],
             date_col: str | None = None, since: str | None = None) -> int:
    if date_col and since:
        # scope the wipe to the collection window: a run with a narrower
        # lookback must not destroy history accumulated by earlier wider runs
        conn.execute(f"DELETE FROM {table} WHERE {date_col}>=?", (since,))
    else:
        conn.execute(f"DELETE FROM {table}")
    if rows:
        ph = ",".join(["?"] * len(cols))
        conn.executemany(
            f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({ph})",
            [tuple(_coerce(r.get(c)) for c in cols) for r in rows],
        )
    conn.commit()
    return len(rows)


def write_commits(conn, rows, since=None):
    """Replace the window's commit rows, carrying each row's first_seen across.

    An `ON CONFLICT … COALESCE(first_seen, excluded.first_seen)` upsert would NOT work
    here: this writer DELETEs the window before inserting (so commits that vanish from
    the default branch after a force-push vanish here too), which means the conflict
    never fires and the previous value is already gone by insert time. So the existing
    stamps are read out first and re-applied by (repo, sha).

    A key already in the table keeps its own stamp — including keeping NULL, which is
    exactly what a row predating the column means. Only keys absent from the table are
    stamped with now. Without that, every nightly collect would re-stamp all ~12k commits
    as "arrived today" and the column would measure nothing at all.
    """
    now = _utc_iso()
    prior = {(r[0], r[1]): r[2] for r in
             conn.execute("SELECT repo, sha, first_seen FROM commits")}
    # .get's default fires only on an ABSENT key; a key present with a NULL stamp
    # correctly yields None again ("was already here, arrival unknown")
    rows = [{**r, "first_seen": prior.get((r["repo"], r["sha"]), now)} for r in rows]
    return _replace(conn, "commits", COMMIT_COLS, rows, "committed_at", since)


def write_prs(conn, rows, since=None):
    return _replace(conn, "pull_request", PR_COLS, rows, "created_at", since)
def write_issues(conn, rows, since=None):
    return _replace(conn, "issue", ISSUE_COLS, rows, "created_at", since)
def write_ci_runs(conn, rows, since=None):
    return _replace(conn, "ci_run", CI_RUN_COLS, rows, "created_at", since)
def write_people_dim(conn, rows):    return _replace(conn, "person", DIM_PERSON_COLS, rows)
def write_repos_dim(conn, rows):     return _replace(conn, "repo", DIM_REPO_COLS, rows)


_GH_PROFILE_FIELDS = ("name", "company", "bio", "location")


def _gh_profile_row(row) -> dict:
    """Row -> {name,company,bio,location}, dropping empty fields."""
    return {f: row[f"gh_{f}"] for f in _GH_PROFILE_FIELDS if row[f"gh_{f}"]}


def gh_profile(conn, login: str) -> dict:
    """GitHub-profile hint for one login from the person dim (DB is the source).
    Empty dict if unknown. Fields the person left blank are omitted."""
    row = conn.execute(
        "SELECT gh_name, gh_company, gh_bio, gh_location FROM person WHERE login=?",
        (login,)).fetchone()
    return _gh_profile_row(row) if row else {}


def gh_profiles(conn) -> dict:
    """login -> GitHub-profile hint, for every person that has any profile field."""
    out = {}
    for row in conn.execute(
            "SELECT login, gh_name, gh_company, gh_bio, gh_location FROM person"):
        prof = _gh_profile_row(row)
        if prof:
            out[row["login"]] = prof
    return out


# ---- human-edit overrides (identity + config), source of truth in the DB -----
def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_overrides(conn, scope: str) -> dict:
    """All overrides for a scope as {key: value_dict}."""
    return {r["key"]: json.loads(r["value"] or "{}")
            for r in conn.execute("SELECT key, value FROM override WHERE scope=?", (scope,))}


def write_override(conn, scope: str, key: str, value: dict, commit: bool = True) -> None:
    conn.execute(
        "INSERT INTO override (scope, key, value, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(scope, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (scope, key, json.dumps(value or {}), _utc_iso()))
    if commit:
        conn.commit()


def delete_override(conn, scope: str, key: str, commit: bool = True) -> None:
    conn.execute("DELETE FROM override WHERE scope=? AND key=?", (scope, key))
    if commit:
        conn.commit()


def replace_overrides(conn, scope: str, rows: dict) -> None:
    """Replace ALL overrides in a scope with `rows` ({key: value_dict}) atomically."""
    ts = _utc_iso()
    with conn:
        conn.execute("DELETE FROM override WHERE scope=?", (scope,))
        conn.executemany(
            "INSERT INTO override (scope, key, value, updated_at) VALUES (?,?,?,?)",
            [(scope, k, json.dumps(v or {}), ts) for k, v in rows.items()])


def overrides_version(conn, scopes: tuple) -> str:
    """A concurrency token for the given override scopes: a content hash of every
    row (scope/key/value/updated_at). Changes on any edit — add, change or delete —
    regardless of timestamp granularity. Embedded in an editor and echoed on Save so
    a stale tab's full-replace is rejected instead of clobbering another session."""
    import hashlib
    placeholders = ",".join("?" * len(scopes))
    rows = conn.execute(
        f"SELECT scope, key, value, updated_at FROM override WHERE scope IN ({placeholders}) "
        "ORDER BY scope, key", tuple(scopes)).fetchall()
    h = hashlib.sha1()
    for r in rows:
        h.update(f"{r['scope']}\x00{r['key']}\x00{r['value']}\x00{r['updated_at']}".encode())
    return h.hexdigest()[:16]


def report_version(conn) -> str:
    """DB-visible version token for the rendered report. Changes on any collect (a
    new/updated run blob) or any override edit (config / taxonomy / identity /
    settings). Unlike the DB file's mtime this is immediate under WAL and independent
    of checkpointing — so it is a sound cache key for live report rendering, where the
    file mtime is not (in-process writes needn't checkpoint the main db file)."""
    import hashlib
    h = hashlib.sha1()
    row = conn.execute(
        "SELECT generated_at FROM runs ORDER BY date DESC LIMIT 1").fetchone()
    h.update((row["generated_at"] if row and row["generated_at"] else "none").encode())
    for r in conn.execute(
            "SELECT scope, key, value, updated_at FROM override ORDER BY scope, key"):
        h.update(f"\x00{r['scope']}\x00{r['key']}\x00{r['value']}\x00{r['updated_at']}".encode())
    return h.hexdigest()[:16]


def override_count(conn, scope: str | None = None) -> int:
    if scope is None:
        return conn.execute("SELECT COUNT(*) FROM override").fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM override WHERE scope=?", (scope,)).fetchone()[0]


# ---- secrets (server-side only; never rendered, never exported) --------------
def set_secret(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO secret (key, value, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, _utc_iso()))
    conn.commit()


def get_secret(conn, key: str):
    row = conn.execute("SELECT value FROM secret WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def clear_secret(conn, key: str) -> None:
    conn.execute("DELETE FROM secret WHERE key=?", (key,))
    conn.commit()


def has_secret(conn, key: str) -> bool:
    return conn.execute("SELECT 1 FROM secret WHERE key=?", (key,)).fetchone() is not None


# seed_overrides_from_yaml() lived here: a per-scope-idempotent import of people.yaml /
# config.local.yaml into the override table, for the one-time move to DB-as-truth. It was
# removed on 2026-07-28 because "seeds only an empty scope" does not make a file safe to
# read — it makes whatever the file happens to contain into curated data. A test had
# written its fixture over the checkout's people.yaml, and the seed then imported it: the
# prod DB carried `person/alice -> {"company": "Constructor"}` (0 commits, 0 PRs, no row in
# the person dim) and `repo/o/lib -> {"classification": "sdk"}` for a repo that does not
# exist. Writing the file was harmless; reading it was the corruption. The override table
# is now the only source, and nothing imports into it.


def write_reviews(conn, rows):
    """Granular PR-review rows (repo, pr_number, reviewer_login, state, submitted_at).
    Idempotent per repo: delete the repos present in this batch, then insert."""
    rows = [r for r in rows if r.get("submitted_at")]
    for rp in {r["repo"] for r in rows}:
        conn.execute("DELETE FROM review WHERE repo=?", (rp,))
    conn.executemany(
        "INSERT INTO review (repo, pr_number, reviewer_login, state, submitted_at) "
        "VALUES (?,?,?,?,?)",
        [(r["repo"], r.get("pr_number"), r.get("reviewer_login"), r.get("state"),
          r["submitted_at"]) for r in rows])
    conn.commit()
    return len(rows)


def write_score_label(conn, subject_login, rater, rating, note=""):
    """Upsert one ground-truth rating (1..5) of `subject_login` by `rater`."""
    conn.execute(
        "INSERT INTO score_label (subject_login, rater, rating, note, created_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(subject_login, rater) DO UPDATE SET "
        "rating=excluded.rating, note=excluded.note, created_at=excluded.created_at",
        (subject_login, rater, int(rating), note or "", _utc_iso()))
    conn.commit()


def read_score_labels(conn) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT subject_login, rater, rating, note, created_at FROM score_label "
        "ORDER BY subject_login, rater")]


def label_summary(conn) -> dict:
    """Per-subject {n, mean} over all raters — the ground-truth target."""
    out: dict = {}
    for r in conn.execute("SELECT subject_login, COUNT(*) n, AVG(rating) m "
                          "FROM score_label GROUP BY subject_login"):
        out[r["subject_login"]] = {"n": r["n"], "mean": round(r["m"], 2)}
    return out


def write_timeline_events(conn, rows):
    """Issue/PR lifecycle events (repo, item_type, number, event, actor_login,
    created_at). Idempotent per repo: replace the repos present in this batch."""
    rows = [r for r in rows if r.get("created_at") and r.get("event")]
    for rp in {r["repo"] for r in rows}:
        conn.execute("DELETE FROM timeline_event WHERE repo=?", (rp,))
    conn.executemany(
        "INSERT OR IGNORE INTO timeline_event "
        "(repo, item_type, number, event, actor_login, created_at) VALUES (?,?,?,?,?,?)",
        [(r["repo"], r.get("item_type"), r.get("number"), r["event"],
          r.get("actor_login"), r["created_at"]) for r in rows])
    conn.commit()
    return len(rows)


# --- the single period aggregator: any [since, until] -> the report's panels -
def _pct(part, whole):
    return round(100 * part / whole, 1) if whole else 0.0


def _day(s: str):
    """Parse a 'YYYY-MM-DD...' string to a date (ignores any time/zone suffix)."""
    from datetime import date
    return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))


def _bucketize(since: str, until: str, by_day: dict, n: int = 12):
    """Distribute per-day (count, loc) pairs into n equal time buckets over
    [since, until] for the KPI activity sparklines. Returns two n-length int lists
    (commits, loc). All same-day rows land together; the span is split evenly."""
    s0, s1 = _day(since), _day(until)
    span = (s1 - s0).days
    cb = [0] * n
    lb = [0] * n
    if span <= 0:                       # zero/negative window: everything in the last slot
        for cnt, loc in by_day.values():
            cb[n - 1] += cnt
            lb[n - 1] += loc
        return cb, lb
    for day, (cnt, loc) in by_day.items():
        try:
            off = (_day(day) - s0).days
        except (ValueError, TypeError):
            continue
        b = int(off / (span + 1) * n)
        b = 0 if b < 0 else (n - 1 if b >= n else b)
        cb[b] += cnt
        lb[b] += loc
    return cb, lb


def _spark_points(vals, w: float = 100.0, h: float = 26.0, pad: float = 3.0) -> str:
    """SVG polyline 'x,y x,y …' for a sparkline over vals; '' when nothing to draw."""
    if not vals or len(vals) < 2 or max(vals) <= 0:
        return ""
    mx = max(vals)
    dx = w / (len(vals) - 1)
    return " ".join(f"{i * dx:.1f},{h - pad - (v / mx) * (h - 2 * pad):.1f}"
                    for i, v in enumerate(vals))


def _repo_type_meta():
    """Configured repo types as [(id, name, color)] for the report split — best-effort
    from config.yaml + overlay; falls back to the built-in platform/app pair."""
    try:
        import collect
        import configstore
        cfg = configstore.apply_overlay(configstore.base_config(), configstore.load_overlay())
        return [(t["id"], t.get("name") or t["id"].title(), t.get("color"))
                for t in collect.repo_types(cfg)]
    except Exception:                       # noqa: BLE001 — never break the report
        return [("platform", "Platform", tokens.ELEMENT_DEFAULTS["platform"]),
            ("app", "App", tokens.ELEMENT_DEFAULTS["app"])]


def aggregate(conn: sqlite3.Connection, since: str, until: str,
              label: str = "all", member_only: bool = False, repos=None,
              trend_gran: str = "auto", trend_dim: str = "company") -> dict:
    """Period-sensitive panels (KPI totals, contribution-by-company, %-by-category,
    work-type) for ANY [since, until] window, computed by SQL over the granular
    tables. Mirrors the shape render expects for one period block. Dates are UTC
    ISO 'YYYY-MM-DDTHH:MM:SSZ'; rows flagged is_bot / is_migration are excluded.

    `repos` (a repo-key list) restricts EVERY windowed query to a slice — None = all
    repos, [] = none. This powers the global slice filter; person/dim lookups stay
    unscoped (a person simply won't appear if none of their scoped rows land)."""
    args = (since, until)
    mem = "AND IFNULL(p.is_member,0)=1" if member_only else ""

    def rf(alias=""):
        """Repo-slice clause for a query; alias '' = unaliased `repo` column."""
        col = (alias + ".") if alias else ""
        if repos is None:
            return ""
        if not repos:
            return " AND 1=0"
        return f" AND {col}repo IN ({','.join('?' * len(repos))})"
    rp = tuple(repos) if repos else ()
    crows = conn.execute(f"""
        SELECT c.author_login login, COUNT(*) commits,
               SUM(c.meaningful_additions) loc, SUM(c.is_spec) specs,
               SUM(c.ai_marked) ai_commits
        FROM commits c LEFT JOIN person p ON p.login = c.author_login
        WHERE c.is_bot=0 AND c.author_login<>'' AND c.committed_at>=? AND c.committed_at<=? {mem}{rf('c')}
        GROUP BY c.author_login""", args + rp).fetchall()
    prrows = conn.execute(f"""
        SELECT pr.author_login login, COUNT(*) prs,
               SUM(CASE WHEN pr.merged_at IS NOT NULL AND pr.merged_at<>'' THEN 1 ELSE 0 END) prs_merged
        FROM pull_request pr LEFT JOIN person p ON p.login = pr.author_login
        WHERE pr.is_bot=0 AND pr.is_migration=0 AND pr.author_login<>''
              AND pr.created_at>=? AND pr.created_at<=? {mem}{rf('pr')}
        GROUP BY pr.author_login""", args + rp).fetchall()
    isrows = conn.execute(f"""
        SELECT i.author_login login, COUNT(*) issues, SUM(i.is_bug) bugs,
               SUM(i.is_feature) features, SUM(i.is_epic) epics
        FROM issue i LEFT JOIN person p ON p.login = i.author_login
        WHERE i.is_bot=0 AND i.is_migration=0 AND i.author_login<>''
              AND i.created_at>=? AND i.created_at<=? {mem}{rf('i')}
        GROUP BY i.author_login""", args + rp).fetchall()
    ctype_rows = conn.execute(f"""
        SELECT c.commit_type t, COUNT(*) n
        FROM commits c LEFT JOIN person p ON p.login = c.author_login
        WHERE c.is_bot=0 AND c.author_login<>'' AND c.committed_at>=? AND c.committed_at<=? {mem}{rf('c')}
        GROUP BY c.commit_type""", args + rp).fetchall()

    comp_of = {r["login"]: (r["company"] or "Other")
               for r in conn.execute("SELECT login, company FROM person")}
    people: dict = {}

    def slot(l):
        return people.setdefault(l, {"login": l, "commits": 0, "loc": 0, "specs": 0,
                                     "ai_commits": 0, "prs": 0, "prs_merged": 0,
                                     "issues": 0, "bugs": 0, "features": 0, "epics": 0})
    for r in crows:
        s = slot(r["login"]); s["commits"] = r["commits"]; s["loc"] = r["loc"] or 0
        s["specs"] = r["specs"] or 0; s["ai_commits"] = r["ai_commits"] or 0
    for r in prrows:
        s = slot(r["login"]); s["prs"] = r["prs"]; s["prs_merged"] = r["prs_merged"] or 0
    for r in isrows:
        s = slot(r["login"]); s["issues"] = r["issues"]
        s["bugs"] = r["bugs"] or 0; s["features"] = r["features"] or 0
        s["epics"] = r["epics"] or 0

    tot = {
        "commits": sum(p["commits"] for p in people.values()),
        "meaningful_additions": sum(p["loc"] for p in people.values()),
        "prs": sum(p["prs"] for p in people.values()),
        "prs_merged": sum(p["prs_merged"] for p in people.values()),
        "specs": sum(p["specs"] for p in people.values()),
        "bugs": sum(p["bugs"] for p in people.values()),
        "features": sum(p["features"] for p in people.values()),
        "epics": sum(p["epics"] for p in people.values()),
        "people": sum(1 for p in people.values()
                      if p["commits"] + p["prs"] + p["issues"] + p["specs"] > 0),
    }

    def ranking(key, total):
        rows = sorted(((p[key], p["login"]) for p in people.values() if p[key]), reverse=True)
        return [{"login": l, "value": v, "pct": _pct(v, total)} for v, l in rows]

    code_total = tot["commits"] + tot["prs"]
    loc_total = tot["meaningful_additions"]
    categories = [
        {"key": "code", "title": "Code", "unit": "commits + PRs", "total": code_total,
         "rows": sorted(({"login": p["login"], "value": p["commits"] + p["prs"],
                          "pct": _pct(p["commits"] + p["prs"], code_total)}
                         for p in people.values() if p["commits"] + p["prs"]),
                        key=lambda x: -x["value"])},
        {"key": "code_loc", "title": "Code (LOC)", "unit": "meaningful LOC added",
         "total": loc_total, "rows": ranking("loc", loc_total or 1)},
        {"key": "specs", "title": "Specs", "unit": "commits to spec docs",
         "total": tot["specs"], "rows": ranking("specs", tot["specs"])},
        {"key": "bugs", "title": "Bugs", "unit": "issues categorised as bug",
         "total": tot["bugs"], "rows": ranking("bugs", tot["bugs"])},
        {"key": "epics", "title": "Epics", "unit": "issues categorised as epic",
         "total": tot["epics"], "rows": ranking("epics", tot["epics"])},
        {"key": "features", "title": "Features", "unit": "issues categorised as feature",
         "total": tot["features"], "rows": ranking("features", tot["features"])},
    ]
    SHOWN = 8
    for cat in categories:
        rows = cat["rows"]
        cat["top3"] = round(sum(r["pct"] for r in rows[:3]), 1)
        cum, n = 0.0, 0
        for r in rows:
            cum += r["pct"]; n += 1
            if cum >= 80:
                break
        cat["n80"] = n
        cat["tail_n"] = max(len(rows) - SHOWN, 0)
        cat["tail_pct"] = round(sum(r["pct"] for r in rows[SHOWN:]), 1)
        cat["tail_value"] = sum(r["value"] for r in rows[SHOWN:])

    comp: dict = {}
    for p in people.values():
        co = comp_of.get(p["login"], "Other")
        a = comp.setdefault(co, {"company": co, "people": 0, "commits": 0,
                                 "meaningful_additions": 0, "specs": 0, "bugs": 0,
                                 "epics": 0, "features": 0, "prs": 0, "ai_commits": 0})
        if p["commits"] + p["prs"] + p["specs"] + p["bugs"] + p["features"] + p["epics"]:
            a["people"] += 1
        a["commits"] += p["commits"]; a["meaningful_additions"] += p["loc"]
        a["specs"] += p["specs"]; a["bugs"] += p["bugs"]; a["epics"] += p["epics"]
        a["features"] += p["features"]; a["prs"] += p["prs"]
        a["ai_commits"] += p["ai_commits"]
    company_rows = sorted(comp.values(), key=lambda x: -x["commits"])
    co_total = sum(c["commits"] for c in company_rows) or 1
    loc_co_total = sum(c["meaningful_additions"] for c in company_rows) or 1
    co_colors = company_color_map([c["company"] for c in company_rows])
    for c in company_rows:
        c["pct"] = _pct(c["commits"], co_total)
        c["loc_pct"] = _pct(c["meaningful_additions"], loc_co_total)
        c["ai_pct"] = _pct(c["ai_commits"], c["commits"])
        c["color"] = co_colors[c["company"]]

    ct_total = sum(r["n"] for r in ctype_rows) or 1
    commit_types = sorted(({"type": r["t"] or "other", "count": r["n"],
                            "pct": _pct(r["n"], ct_total)} for r in ctype_rows),
                          key=lambda x: (x["type"] == "other", -x["count"]))

    # ---- commit mix (code vs specs) for the window -----------------------
    ct_all = tot["commits"]; spec_c = tot["specs"]; code_c = max(ct_all - spec_c, 0)
    pct_specs = _pct(spec_c, ct_all); circ = round(2 * 3.14159265 * 54, 2)
    commit_mix = {"total": ct_all, "code": code_c, "specs": spec_c,
                  "pct_specs": pct_specs, "pct_code": round(100 - pct_specs, 1),
                  "circ": circ, "specs_len": round(circ * pct_specs / 100, 2),
                  "code_len": round(circ * (100 - pct_specs) / 100, 2)}

    # ---- repo-type split (commits / LOC / PRs) in the window — N-way -----
    by_cls_c: dict = {}          # classification -> [commits, meaningful_loc]
    for r in conn.execute(f"""
        SELECT c.classification cls, COUNT(*) n, SUM(c.meaningful_additions) loc
        FROM commits c LEFT JOIN person p ON p.login = c.author_login
        WHERE c.is_bot=0 AND c.author_login<>'' AND c.committed_at>=? AND c.committed_at<=? {mem}{rf('c')}
        GROUP BY c.classification""", args + rp):
        by_cls_c[r["cls"] or ""] = [r["n"], r["loc"] or 0]
    by_cls_p: dict = {}          # classification -> prs
    for r in conn.execute(f"""
        SELECT pr.classification cls, COUNT(*) n
        FROM pull_request pr LEFT JOIN person p ON p.login = pr.author_login
        WHERE pr.is_bot=0 AND pr.is_migration=0 AND pr.author_login<>''
              AND pr.created_at>=? AND pr.created_at<=? {mem}{rf('pr')}
        GROUP BY pr.classification""", args + rp):
        by_cls_p[r["cls"] or ""] = r["n"]

    _PAL = tokens.CATEGORY_SWATCHES
    meta = _repo_type_meta()                 # [(id, name, color)] from config
    meta_ids = {m[0] for m in meta}
    extra = sorted(c for c in (set(by_cls_c) | set(by_cls_p))
                   if c and c not in meta_ids and c not in ("ignore", "unclassified"))
    ordered = list(meta) + [(c, c.replace("-", " ").replace("_", " ").title(), None) for c in extra]
    type_list = []
    for i, (tid, tname, tcolor) in enumerate(ordered):
        c = by_cls_c.get(tid, [0, 0])
        type_list.append({"id": tid, "name": tname, "color": tcolor or _PAL[i % len(_PAL)],
                          "commits": c[0], "loc": c[1], "prs": by_cls_p.get(tid, 0)})
    # backward-compat platform/app keys (per-person + person page still read these in B1)
    pcm, acm = by_cls_c.get("platform", [0, 0])[0], by_cls_c.get("app", [0, 0])[0]
    plo, alo = by_cls_c.get("platform", [0, 0])[1], by_cls_c.get("app", [0, 0])[1]
    ppr, apr = by_cls_p.get("platform", 0), by_cls_p.get("app", 0)
    split = {"types": type_list,
             "commits_total": sum(t["commits"] for t in type_list),
             "loc_total": sum(t["loc"] for t in type_list),
             "prs_total": sum(t["prs"] for t in type_list),
             "commits": {"platform": pcm, "app": acm, "pct_platform": _pct(pcm, pcm + acm)},
             "prs": {"platform": ppr, "app": apr, "pct_platform": _pct(ppr, ppr + apr)},
             "loc": {"platform": plo, "app": alo, "pct_platform": _pct(plo, plo + alo)}}

    # ---- per-login repo-type footprint (commits+PRs by type) + raw additions ----
    pa: dict = {}
    for r in conn.execute(f"""
        SELECT c.author_login login, c.classification cls, COUNT(*) n, SUM(c.additions) raw
        FROM commits c LEFT JOIN person p ON p.login = c.author_login
        WHERE c.is_bot=0 AND c.author_login<>'' AND c.committed_at>=? AND c.committed_at<=? {mem}{rf('c')}
        GROUP BY c.author_login, c.classification""", args + rp):
        s = pa.setdefault(r["login"], {"raw": 0, "by_type": {}})
        s["raw"] += r["raw"] or 0
        if r["cls"]:
            s["by_type"][r["cls"]] = s["by_type"].get(r["cls"], 0) + r["n"]
    for r in conn.execute(f"""
        SELECT pr.author_login login, pr.classification cls, COUNT(*) n
        FROM pull_request pr LEFT JOIN person p ON p.login = pr.author_login
        WHERE pr.is_bot=0 AND pr.is_migration=0 AND pr.author_login<>''
              AND pr.created_at>=? AND pr.created_at<=? {mem}{rf('pr')}
        GROUP BY pr.author_login, pr.classification""", args + rp):
        s = pa.setdefault(r["login"], {"raw": 0, "by_type": {}})
        if r["cls"]:
            s["by_type"][r["cls"]] = s["by_type"].get(r["cls"], 0) + r["n"]

    # ---- per-person master rows (window activity + all-time sticky cols) --
    dim = {r["login"]: r for r in conn.execute("SELECT * FROM person")}
    people_rows = []
    for lg, p in people.items():
        d0 = dim.get(lg)
        a = pa.get(lg, {"raw": 0, "by_type": {}})
        cm = p["commits"]; sp = p["specs"]
        people_rows.append({
            "login": lg, "name": (d0["name"] if d0 else "") or "",
            "company": comp_of.get(lg, "Other"),
            "is_member": bool(d0["is_member"]) if d0 else False,
            "klass": "member" if (d0 and d0["is_member"]) else "external",
            "commits": cm, "loc": p["loc"], "raw_loc": a["raw"] or p["loc"],
            "prs": p["prs"], "merged_prs": p["prs_merged"],
            "specs": sp, "bugs": p["bugs"], "features": p["features"], "epics": p["epics"],
            "by_type": a.get("by_type", {}),
            "ai_commits": p["ai_commits"],
            "reviews": (d0["reviews_given"] if d0 else 0) or 0,
            "approvals": (d0["approvals_given"] if d0 else 0) or 0,
            "ttm": (d0["median_ttm_h"] if d0 else None),
            "cpt_lines": (d0["cpt_lines"] if d0 else 0) or 0,
            "surv_code_human": (d0["surviving_code_human"] if d0 else 0) or 0,
            "surv_code_ai": (d0["surviving_code_ai"] if d0 else 0) or 0,
            "surv_spec": (d0["surviving_spec"] if d0 else 0) or 0,
            "surv_win_code": None,  # blame-in-window not derivable per arbitrary period
            "code_commits": max(cm - sp, 0),
            "mix_specs_pct": round(100 * sp / cm, 1) if cm else 0,
            "mix_code_pct": round(100 * max(cm - sp, 0) / cm, 1) if cm else 0,
        })
    people_rows.sort(key=lambda x: (-(x["commits"] + x["prs"]), -x["surv_code_human"]))

    # ---- by-Element rollup: window activity + all-time KLOC (repo dim) ----
    def _dtp(s):
        try:
            return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
        except Exception:
            return None
    el: dict = {}

    def eslot(e):
        return el.setdefault(e, {"element": e, "commits_window": 0, "ai": 0,
                                 "code_loc": 0, "spec_loc": 0, "repos": 0,
                                 "members": set(), "externals": set(),
                                 "prs_opened_window": 0, "prs_merged_window": 0, "ttms": []})
    for r in conn.execute(
            "SELECT element, code_loc, spec_loc FROM repo "
            "WHERE element IS NOT NULL AND element<>''"
            + (rf().replace("repo IN", "key IN") if repos is not None else ""), rp):
        s = eslot(r["element"]); s["repos"] += 1
        s["code_loc"] += r["code_loc"] or 0; s["spec_loc"] += r["spec_loc"] or 0
    ismem = {r["login"]: r["is_member"] for r in conn.execute("SELECT login, is_member FROM person")}
    for r in conn.execute(f"""
        SELECT rr.element el, c.author_login login, COUNT(*) n, SUM(c.ai_marked) ai
        FROM commits c JOIN repo rr ON rr.key = c.repo
             LEFT JOIN person p ON p.login = c.author_login
        WHERE c.is_bot=0 AND c.author_login<>'' AND rr.element IS NOT NULL AND rr.element<>''
              AND c.committed_at>=? AND c.committed_at<=? {mem}{rf('c')}
        GROUP BY rr.element, c.author_login""", args + rp):
        s = eslot(r["el"]); s["commits_window"] += r["n"]; s["ai"] += r["ai"] or 0
        (s["members"] if ismem.get(r["login"]) else s["externals"]).add(r["login"])
    for r in conn.execute(f"""
        SELECT rr.element el, pr.created_at, pr.merged_at
        FROM pull_request pr JOIN repo rr ON rr.key = pr.repo
             LEFT JOIN person p ON p.login = pr.author_login
        WHERE pr.is_bot=0 AND pr.is_migration=0 AND rr.element IS NOT NULL AND rr.element<>''
              AND pr.created_at>=? AND pr.created_at<=? {mem}{rf('pr')}""", args + rp):
        s = eslot(r["el"]); s["prs_opened_window"] += 1
        if r["merged_at"]:
            s["prs_merged_window"] += 1
            t0, t1 = _dtp(r["created_at"]), _dtp(r["merged_at"])
            if t0 and t1:
                s["ttms"].append((t1 - t0).total_seconds() / 3600)
    element_rows = []
    for s in el.values():
        med = round(statistics.median(s["ttms"]), 1) if s["ttms"] else None
        element_rows.append({
            "element": s["element"], "code_kloc": round(s["code_loc"] / 1000, 1),
            "spec_kloc": round(s["spec_loc"] / 1000, 1),
            "code_loc": s["code_loc"], "spec_loc": s["spec_loc"], "repos": s["repos"],
            "people_members": len(s["members"]), "people_external": len(s["externals"]),
            "commits_window": s["commits_window"], "prs_opened_window": s["prs_opened_window"],
            "prs_merged_window": s["prs_merged_window"], "median_ttm_h": med,
            "ai_pct": _pct(s["ai"], s["commits_window"])})
    element_rows.sort(key=lambda x: -x["commits_window"])

    # ---- traffic summed over the window (accumulated daily rows) ---------
    trows = []
    for r in conn.execute(f"""
        SELECT repo, SUM(clones) clones, SUM(clone_uniques) uniques,
               SUM(views) views, SUM(view_uniques) visitors
        FROM traffic WHERE date>=? AND date<=?{rf('')} GROUP BY repo ORDER BY SUM(clones) DESC""",
                          (since[:10], until[:10]) + rp):
        trows.append({"name": r["repo"].split("/")[-1], "clones": r["clones"] or 0,
                      "uniques": r["uniques"] or 0, "views": r["views"] or 0,
                      "visitors": r["visitors"] or 0, "daily": [], "paths": [],
                      "contributors": 0})
    earliest = conn.execute("SELECT MIN(date) m FROM traffic").fetchone()["m"]
    traffic = {"total_clones": sum(t["clones"] for t in trows),
               "unique_cloners": sum(t["uniques"] for t in trows),
               "total_views": sum(t["views"] for t in trows),
               "total_visitors": sum(t["visitors"] for t in trows),
               "n_repos": len(trows), "n_no_access": 0, "rows": trows,
               "daily_max": 1, "since": earliest, "windowed": True}

    # ---- AI-tool usage headline for the window (marker floor) ------------
    ai_any = sum(p["ai_commits"] for p in people.values())
    # Per-tool split for the window. ai_marked rows with empty ai_tools predate
    # the ai_tools column — the split is unknowable until the next collect run,
    # so signal that with tools=None (template shows a "needs refresh" note).
    tool_agg: dict = {}
    for r in conn.execute(f"""
        SELECT ai_tools, COUNT(*) commits, SUM(ai_loc) loc
        FROM commits WHERE is_bot=0 AND ai_marked=1 AND IFNULL(ai_tools,'')<>''
              AND committed_at>=? AND committed_at<=?{rf('')}
        GROUP BY ai_tools""", args + rp):
        for t in r["ai_tools"].split(","):
            x = tool_agg.setdefault(t, {"tool": t, "commits": 0, "loc": 0})
            x["commits"] += r["commits"]
            x["loc"] += r["loc"] or 0
    tools = sorted(tool_agg.values(), key=lambda x: -x["commits"])
    for x in tools:
        x["pct"] = _pct(x["commits"], tot["commits"])
    ai_usage = {"any_commits": ai_any, "total_commits": tot["commits"],
                "pct": _pct(ai_any, tot["commits"]),
                "tools": tools if (tools or not ai_any) else None,
                "windowed": True}

    # ---- bot activity in the window (excluded from human metrics) --------
    brows: dict = {}
    for r in conn.execute(f"""
        SELECT author_login login, COUNT(*) commits, SUM(additions) loc,
               SUM(ai_marked) ai, COUNT(DISTINCT repo) repos
        FROM commits WHERE is_bot=1 AND author_login<>''
              AND committed_at>=? AND committed_at<=?{rf('')}
        GROUP BY author_login ORDER BY COUNT(*) DESC""", args + rp):
        brows[r["login"]] = {"login": r["login"], "commits": r["commits"],
                             "additions": r["loc"] or 0, "ai_commits": r["ai"] or 0,
                             "repos": r["repos"], "reviews": None, "activity": "commits"}
    for r in conn.execute(f"""
        SELECT author_login login, COUNT(*) prs FROM pull_request
        WHERE is_bot=1 AND author_login<>'' AND created_at>=? AND created_at<=?{rf('')}
        GROUP BY author_login""", args + rp):
        b = brows.setdefault(r["login"], {"login": r["login"], "commits": 0,
                                          "additions": 0, "ai_commits": 0, "repos": 0,
                                          "reviews": None, "activity": "PRs"})
        b["prs"] = r["prs"]
    bot_rows = sorted(brows.values(), key=lambda x: -x["commits"])
    bots = {"count": len(bot_rows),
            "commits": sum(b["commits"] for b in bot_rows),
            "additions": sum(b["additions"] for b in bot_rows),
            "reviews": None, "rows": bot_rows, "windowed": True}

    # ---- code review in the window (from the granular `review` table) ----
    # reviewers / approvals = review ACTIVITY submitted in the window.
    rvr: dict = {}
    for r in conn.execute(f"""
        SELECT reviewer_login login, state FROM review
        WHERE submitted_at>=? AND submitted_at<=? AND reviewer_login<>''{rf('')}""", args + rp):
        s = rvr.setdefault(r["login"], {"login": r["login"], "reviews": 0,
                                        "approvals": 0, "latency_h": None})
        s["reviews"] += 1
        if r["state"] == "APPROVED":
            s["approvals"] += 1
    reviewers = sorted(rvr.values(), key=lambda x: -x["reviews"])
    # coverage = share of PRs OPENED in the window that got any review. Numerator
    # and denominator MUST be the same population (PRs created in the window), else
    # a PR reviewed-but-not-created-in-window pushes coverage past 100%. The review
    # table is already bot/migration-free (filtered at collect).
    window_prs = {(r["repo"], r["number"]) for r in conn.execute(
        "SELECT repo, number FROM pull_request WHERE is_bot=0 AND is_migration=0 "
        "AND created_at>=? AND created_at<=?" + rf(''), args + rp)}
    reviewed_any = {(r["repo"], r["pr_number"]) for r in conn.execute(
        "SELECT DISTINCT repo, pr_number FROM review WHERE reviewer_login<>''")}
    reviewed_in_window = window_prs & reviewed_any
    ttms2 = []
    merged2 = 0
    for r in conn.execute(f"""
        SELECT created_at, merged_at FROM pull_request
        WHERE is_bot=0 AND is_migration=0 AND created_at>=? AND created_at<=?
              AND merged_at IS NOT NULL AND merged_at<>''{rf('')}""", args + rp):
        merged2 += 1
        t0, t1 = _dtp(r["created_at"]), _dtp(r["merged_at"])
        if t0 and t1:
            ttms2.append((t1 - t0).total_seconds() / 3600)
    reviews = {"total_prs": len(window_prs), "reviewed_prs": len(reviewed_in_window),
               "coverage_pct": _pct(len(reviewed_in_window), len(window_prs)),
               "median_ttm_h": round(statistics.median(ttms2), 1) if ttms2 else None,
               "merged": merged2, "reviewers": reviewers, "windowed": True}
    rc: dict = {}
    for v in rvr.values():
        co = comp_of.get(v["login"], "Other")
        a = rc.setdefault(co, {"company": co, "reviews": 0, "approvals": 0,
                               "review_latency_h": None, "median_ttm_h": None, "merged": 0})
        a["reviews"] += v["reviews"]; a["approvals"] += v["approvals"]
    reviews_by_company = sorted(rc.values(), key=lambda x: -x["reviews"])

    # ---- in-window activity sparklines (12 buckets) for every headline KPI ----
    def _kpi_series(table, datecol, base):
        rows = conn.execute(
            f"SELECT substr({datecol},1,10) d, COUNT(*) n FROM {table} "
            f"WHERE {base} AND {datecol}>=? AND {datecol}<=?{rf('')} GROUP BY d",
            args + rp).fetchall()
        return _bucketize(since, until, {r["d"]: (r["n"], 0) for r in rows})[0]

    spark_rows = conn.execute(f"""
        SELECT substr(c.committed_at, 1, 10) d, COUNT(*) n,
               IFNULL(SUM(c.meaningful_additions), 0) loc
        FROM commits c
        WHERE c.is_bot=0 AND c.author_login<>'' AND c.committed_at>=? AND c.committed_at<=?{rf('c')}
        GROUP BY d""", args + rp).fetchall()
    bcommits, bloc = _bucketize(since, until,
                                {r["d"]: (r["n"], r["loc"] or 0) for r in spark_rows})
    bspecs = _kpi_series("commits", "committed_at", "is_bot=0 AND author_login<>'' AND is_spec=1")
    bprs = _kpi_series("pull_request", "created_at", "is_bot=0 AND is_migration=0 AND author_login<>''")
    bbugs = _kpi_series("issue", "created_at", "is_bot=0 AND is_migration=0 AND author_login<>'' AND is_bug=1")
    bepics = _kpi_series("issue", "created_at", "is_bot=0 AND is_migration=0 AND author_login<>'' AND is_epic=1")
    bstories = _kpi_series("issue", "created_at", "is_bot=0 AND is_migration=0 AND author_login<>'' AND is_feature=1")
    # active people per bucket = distinct logins active (commit/PR/issue) in that bucket
    ppl_events = conn.execute(f"""
        SELECT substr(committed_at,1,10) d, author_login l FROM commits
          WHERE is_bot=0 AND author_login<>'' AND committed_at>=? AND committed_at<=?{rf('')}
        UNION ALL SELECT substr(created_at,1,10), author_login FROM pull_request
          WHERE is_bot=0 AND is_migration=0 AND author_login<>'' AND created_at>=? AND created_at<=?{rf('')}
        UNION ALL SELECT substr(created_at,1,10), author_login FROM issue
          WHERE is_bot=0 AND is_migration=0 AND author_login<>'' AND created_at>=? AND created_at<=?{rf('')}
    """, args + rp + args + rp + args + rp).fetchall()
    _s0 = _day(since); _span = max((_day(until) - _s0).days, 0)
    _pb = [set() for _ in range(12)]
    for r in ppl_events:
        try:
            off = (_day(r["d"]) - _s0).days
        except (ValueError, TypeError):
            continue
        b = 0 if _span <= 0 else int(off / (_span + 1) * 12)
        _pb[0 if b < 0 else (11 if b >= 11 else b)].add(r["l"])
    bpeople = [len(s) for s in _pb]
    spark = {"commits": bcommits, "loc": bloc,
             "commits_pts": _spark_points(bcommits), "loc_pts": _spark_points(bloc),
             "prs_pts": _spark_points(bprs), "specs_pts": _spark_points(bspecs),
             "bugs_pts": _spark_points(bbugs), "features_pts": _spark_points(bstories),
             "epics_pts": _spark_points(bepics), "people_pts": _spark_points(bpeople)}

    return {"label": label, "totals": tot, "categories": categories,
            "company_rows": company_rows, "commit_types": commit_types,
            "loc_added_h": f"{tot['meaningful_additions']:,}",
            "people": people_rows, "commit_mix": commit_mix, "split": split,
            "element_rows": element_rows, "traffic": traffic,
            "ai_usage": ai_usage, "bots": bots, "spark": spark,
            "weekly": weekly_activity(conn, since, until, repos),
            "ctrend": trend_block(conn, since, until, repos, trend_gran, trend_dim),
            "worktype_break": worktype_breakdown(conn, since, until, repos),
            "reviews": reviews, "reviews_by_company": reviews_by_company}


# --- drill-down: the rows behind a number, each linking to GitHub ------------
_DRILL = {
    "commit": {"table": "commits", "date": "committed_at", "ref": "sha", "kind": "commit",
               "base": "is_bot=0 AND author_login<>''",
               "cols": "repo, sha, author_login, committed_at, meaningful_additions, "
                       "commit_type, is_spec, ai_marked, title"},
    "pr": {"table": "pull_request", "date": "created_at", "ref": "number", "kind": "pull",
           "base": "is_bot=0 AND is_migration=0 AND author_login<>''",
           "cols": "repo, number, author_login, created_at, state, merged_at, additions, "
                   "changed_files, title"},
    "issue": {"table": "issue", "date": "created_at", "ref": "number", "kind": "issues",
              "base": "is_bot=0 AND is_migration=0 AND author_login<>''",
              "cols": "repo, number, author_login, created_at, state, is_bug, "
                      "is_feature, is_epic, issue_type, title"},
}
_DRILL_FLAGS = {"is_spec", "is_bug", "is_feature", "is_epic", "ai_marked", "is_revert",
                "is_draft"}


_WK_SERIES = [("commits", "Commits"), ("specs", "Specs"), ("prs", "PRs"), ("issues", "Issues")]


def _wk_of(day: str):
    """'YYYY-MM-DD' → 'YYYY-Www' ISO-week key (matches collect.iso_week), or None."""
    try:
        y, w, _ = date.fromisoformat(day).isocalendar()
        return f"{y}-W{w:02d}"
    except (ValueError, TypeError):
        return None


def weekly_activity(conn, since: str, until: str, repos=None) -> dict:
    """Activity-by-week buckets (commits/specs/PRs/issues) for the window and optional
    repo slice, formatted exactly as the render panel expects (weeks, wlabels, axis,
    max, rows). Derived live from the granular tables, so it follows the period AND the
    slice — unlike the snapshot trend, which is org-wide historical."""
    rfilter, rparams = _repo_filter(repos, "repo")

    def bucket(table, datecol, base):
        out: dict = {}
        q = (f"SELECT substr({datecol},1,10) d, COUNT(*) n FROM {table} "
             f"WHERE {base} AND {datecol}>=? AND {datecol}<=?{rfilter} GROUP BY d")
        for day, n in conn.execute(q, (since, until) + rparams):
            wk = _wk_of(day or "")
            if wk:
                out[wk] = out.get(wk, 0) + n
        return out

    raw = {
        "commits": bucket("commits", "committed_at", "is_bot=0 AND author_login<>''"),
        "specs": bucket("commits", "committed_at", "is_bot=0 AND author_login<>'' AND is_spec=1"),
        "prs": bucket("pull_request", "created_at", "is_bot=0 AND is_migration=0 AND author_login<>''"),
        "issues": bucket("issue", "created_at", "is_bot=0 AND is_migration=0 AND author_login<>''"),
    }
    weeks = sorted({w for cat in raw.values() for w in cat})
    MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    def wk_start(iso):
        y, w = iso.split("-W")
        return date.fromisocalendar(int(y), int(w), 1)

    def fmt(dte):
        return f"{dte.day} {MON[dte.month - 1]}"

    starts = [wk_start(w) for w in weeks]
    wlabels = [f"{fmt(s)} – {fmt(s + timedelta(days=6))}" for s in starts]
    wlabels_short = [fmt(s) for s in starts]      # week-start only, for compact chart axes
    axis, prev_m = [], None
    for i, s in enumerate(starts):
        show = i == 0 or i == len(starts) - 1 or s.month != prev_m
        axis.append(fmt(s) if show else "")
        prev_m = s.month
    vals = [c for cat in raw.values() for c in cat.values()]
    return {
        "weeks": weeks, "wlabels": wlabels, "wlabels_short": wlabels_short, "axis": axis,
        "max": max(vals) if vals else 1,
        "rows": [{"key": k, "title": t, "vals": [raw.get(k, {}).get(w, 0) for w in weeks]}
                 for k, t in _WK_SERIES],
    }


_TREND_GRANS = ("day", "week", "month", "quarter")


def _resolve_trend_gran(since: str, until: str, gran: str) -> str:
    """Pick a bucket size. Explicit day/week/month/quarter is honoured; 'auto' (or
    anything else) scales to the window span so short windows don't collapse to one
    bar and long ones don't produce hundreds."""
    if gran in _TREND_GRANS:
        return gran
    try:
        from datetime import date
        span = (date.fromisoformat(until[:10]) - date.fromisoformat(since[:10])).days
    except Exception:                                   # noqa: BLE001
        span = 10 ** 6
    if span <= 45:
        return "day"
    if span <= 240:
        return "week"
    if span <= 1300:
        return "month"
    return "quarter"


_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _bucket_expr(col: str, gran: str) -> str:
    """SQLite expression bucketing an ISO-timestamp column at `gran` (all keys sort
    as plain strings)."""
    return {
        "day": "substr(%s,1,10)" % col,
        "week": ("date(%s,'-'||((cast(strftime('%%w',%s) as int)+6)%%7)||' days')"
                 % (col, col)),                            # Monday of the week
        "month": "substr(%s,1,7)" % col,
        "quarter": ("substr(%s,1,4)||'-Q'||((cast(substr(%s,6,2) as int)+2)/3)"
                    % (col, col)),
    }[gran]


def _bucket_label(key: str, gran: str) -> str:
    if gran in ("day", "week"):                            # key = YYYY-MM-DD
        y, mm, dd = key.split("-")
        return f"{int(dd)} {_MON[int(mm) - 1]}"
    if gran == "quarter":                                  # key = YYYY-Qn
        y, q = key.split("-")
        return f"{q} '{y[2:]}"
    y, mm = key.split("-")                                 # key = YYYY-MM
    return f"{_MON[int(mm) - 1]} {y[2:]}"


# The dimensions the main stacked-area trend can be broken down by. Each maps to a
# SQL group expression over `commits c` (person p / repo rp are always left-joined).
_TREND_DIMS = {
    "company": "IFNULL(NULLIF(p.company,''),'Other')",
    "work_type": "IFNULL(NULLIF(c.commit_type,''),'other')",
    "repo_type": "IFNULL(NULLIF(c.classification,''),'unclassified')",
    "element": "IFNULL(NULLIF(rp.element,''),'Other')",
}


def _repo_filter(repos, col="c.repo"):
    if repos is None:
        return "", ()
    if not repos:
        return " AND 1=0", ()
    return (" AND %s IN (%s)" % (col, ",".join("?" * len(repos))), tuple(repos))


def company_trend(conn, since, until, repos=None, gran="auto") -> dict:
    """Back-compat shim: the commits/LOC-by-company block, now a slice of trend_block."""
    return trend_block(conn, since, until, repos, gran, "company")


def trend_block(conn, since: str, until: str, repos=None,
                gran: str = "auto", dim: str = "company") -> dict:
    """Everything the Trend tab needs, bucketed over the window + slice at day / week /
    month / quarter (gran='auto' scales to the span). Returns, on ONE shared bucket
    axis: the main commits & meaningful-LOC series broken down by `dim` (company /
    work_type / repo_type / element), PR throughput (opened vs merged + median
    time-to-merge), and the active-contributor count. All derived live from the
    granular tables so they follow the period AND the slice."""
    resolved = _resolve_trend_gran(since, until, gran)
    dim = dim if dim in _TREND_DIMS else "company"
    rf, rp = _repo_filter(repos)
    cb = _bucket_expr("c.committed_at", resolved)

    # 1) main: commits + LOC per (bucket, dimension member)
    main_rows = conn.execute(
        "SELECT " + cb + " b, " + _TREND_DIMS[dim] + " k, "
        "COUNT(*) n, IFNULL(SUM(c.meaningful_additions),0) loc "
        "FROM commits c LEFT JOIN person p ON p.login=c.author_login "
        "LEFT JOIN repo rp ON rp.key=c.repo "
        "WHERE c.is_bot=0 AND c.author_login<>'' AND c.committed_at>=? AND c.committed_at<=?"
        + rf + " GROUP BY b, k", (since, until) + rp).fetchall()

    # 2) active contributors per bucket
    contrib = {r["b"]: r["n"] for r in conn.execute(
        "SELECT " + cb + " b, COUNT(DISTINCT c.author_login) n FROM commits c "
        "WHERE c.is_bot=0 AND c.author_login<>'' AND c.committed_at>=? AND c.committed_at<=?"
        + rf + " GROUP BY b", (since, until) + rp).fetchall() if r["b"]}

    # 3) PR throughput: opened by created_at, merged by merged_at, + per-PR TTM hours
    prf, prp = _repo_filter(repos, "repo")
    ob = _bucket_expr("created_at", resolved)
    opened = {r["b"]: r["n"] for r in conn.execute(
        "SELECT " + ob + " b, COUNT(*) n FROM pull_request "
        "WHERE is_bot=0 AND is_migration=0 AND created_at<>'' AND created_at>=? AND created_at<=?"
        + prf + " GROUP BY b", (since, until) + prp).fetchall() if r["b"]}
    mb = _bucket_expr("merged_at", resolved)
    merged_rows = conn.execute(
        "SELECT " + mb + " b, (julianday(merged_at)-julianday(created_at))*24.0 h "
        "FROM pull_request WHERE is_bot=0 AND is_migration=0 AND merged_at<>'' "
        "AND created_at<>'' AND merged_at>=? AND merged_at<=?"
        + prf + " GROUP BY repo, number", (since, until) + prp).fetchall()
    merged: dict = {}
    ttm_samples: dict = {}
    for r in merged_rows:
        if not r["b"]:
            continue
        merged[r["b"]] = merged.get(r["b"], 0) + 1
        if r["h"] is not None and r["h"] >= 0:
            ttm_samples.setdefault(r["b"], []).append(r["h"])

    # shared bucket axis = union of every series' buckets, chronological
    buckets = sorted(set(r["b"] for r in main_rows if r["b"])
                     | set(contrib) | set(opened) | set(merged))
    dates = [_bucket_label(b, resolved) for b in buckets]

    # main series, dimension members ordered by total commits (stable across buckets)
    by_k_c: dict = {}
    by_k_l: dict = {}
    totals: dict = {}
    for r in main_rows:
        if not r["b"]:
            continue
        by_k_c.setdefault(r["k"], {})[r["b"]] = r["n"]
        by_k_l.setdefault(r["k"], {})[r["b"]] = r["loc"]
        totals[r["k"]] = totals.get(r["k"], 0) + r["n"]
    order = sorted(totals, key=lambda k: -totals[k])
    label_of = _dim_labeller(dim)

    def series(src):
        return [{"company": label_of(k), "key": k,
                 "vals": [src.get(k, {}).get(b, 0) for b in buckets]} for k in order]

    def median(xs):
        xs = sorted(xs)
        m = len(xs)
        if not m:
            return None
        return xs[m // 2] if m % 2 else (xs[m // 2 - 1] + xs[m // 2]) / 2

    return {
        "points": len(buckets), "dates": dates,
        "gran": resolved, "gran_req": (gran if gran in _TREND_GRANS else "auto"),
        "dim": dim, "dims": [{"key": d, "label": _DIM_LABELS[d]} for d in _TREND_DIM_ORDER],
        "commit_rows": series(by_k_c), "loc_rows": series(by_k_l),
        "throughput": {
            "opened": [opened.get(b, 0) for b in buckets],
            "merged": [merged.get(b, 0) for b in buckets],
            "ttm": [round(median(ttm_samples.get(b, [])), 1)
                    if ttm_samples.get(b) else None for b in buckets],
        },
        "contributors": [contrib.get(b, 0) for b in buckets],
    }


_DIM_LABELS = {"company": "Company", "work_type": "Work type",
               "repo_type": "Repo type", "element": "Element"}
_TREND_DIM_ORDER = ["company", "work_type", "repo_type", "element"]


def worktype_breakdown(conn, since: str, until: str, repos=None) -> dict:
    """Commit-type split by company and by repo for the window + slice, from the
    granular commits table (bots excluded, so it matches the work-type drill).
    Rows ordered by total; type columns ordered by overall volume."""
    rf, rp = _repo_filter(repos)
    co_rows = conn.execute(
        "SELECT IFNULL(NULLIF(p.company,''),'Other') co, "
        "IFNULL(NULLIF(c.commit_type,''),'other') t, COUNT(*) n "
        "FROM commits c LEFT JOIN person p ON p.login=c.author_login "
        "WHERE c.is_bot=0 AND c.author_login<>'' AND c.committed_at>=? AND c.committed_at<=?"
        + rf + " GROUP BY co, t", (since, until) + rp).fetchall()
    rp_rows = conn.execute(
        "SELECT c.repo repo, rp.name name, rp.legacy_only legacy, "
        "IFNULL(NULLIF(c.commit_type,''),'other') t, COUNT(*) n "
        "FROM commits c LEFT JOIN repo rp ON rp.key=c.repo "
        "WHERE c.is_bot=0 AND c.author_login<>'' AND c.committed_at>=? AND c.committed_at<=?"
        + rf + " GROUP BY c.repo, t", (since, until) + rp).fetchall()
    ttot: dict = {}
    by_company: dict = {}
    for r in co_rows:
        e = by_company.setdefault(r["co"], {"company": r["co"], "types": {}, "total": 0})
        e["types"][r["t"]] = e["types"].get(r["t"], 0) + r["n"]
        e["total"] += r["n"]
        ttot[r["t"]] = ttot.get(r["t"], 0) + r["n"]
    by_repo: dict = {}
    for r in rp_rows:
        key = r["repo"]
        e = by_repo.setdefault(key, {"repo": r["name"] or key.split("/")[-1],
                                     "key": key, "legacy": bool(r["legacy"]),
                                     "types": {}, "total": 0})
        e["types"][r["t"]] = e["types"].get(r["t"], 0) + r["n"]
        e["total"] += r["n"]
    return {
        "type_cols": [t for t, _ in sorted(ttot.items(), key=lambda kv: -kv[1])],
        "by_company": sorted(by_company.values(), key=lambda x: -x["total"]),
        "by_repo": sorted(by_repo.values(), key=lambda x: -x["total"]),
    }


def _dim_labeller(dim: str):
    """Map a raw group key to its display label. Repo-type ids resolve to their
    configured names; the rest display as-is."""
    if dim == "repo_type":
        names = {tid: name for tid, name, _ in _repo_type_meta()}
        return lambda k: names.get(k, "Unclassified" if k == "unclassified" else str(k).title())
    if dim == "work_type":
        return lambda k: str(k).upper() if len(str(k)) <= 2 else str(k).title()
    return lambda k: k


def drill(conn, entity: str, since: str, until: str, repos=None, author: str = "",
          company: str = "", classification: str = "", flag: str = "",
          commit_type: str = "", pr_state: str = "", ai_tool: str = "", spec: str = "",
          reviewed: str = "", abandon_reason: str = "",
          limit: int = 500, offset: int = 0) -> dict:
    """The individual commit/PR/issue rows behind a metric, with a GitHub URL each.
    Same base filters as aggregate() so counts match; extra filters narrow to a specific
    tile: author, company, classification (platform/app), a boolean flag, commit_type
    (feat/fix/…), pr_state (merged/abandoned/open/open_unreviewed), abandon_reason
    (why a PR was closed unmerged — see ABANDON_REASONS), ai_tool (per-tool AI
    split), spec ('1'
    spec-only / '0' code-only). Newest first, capped."""
    d = _DRILL.get(entity)
    if not d:
        return {"error": f"unknown entity {entity!r}"}
    where = [d["base"], f"{d['date']}>=?", f"{d['date']}<=?"]
    params: list = [since, until]
    if repos is not None:
        if not repos:
            where.append("1=0")
        else:
            where.append("repo IN (%s)" % ",".join("?" * len(repos)))
            params += list(repos)
    if author:
        where.append("author_login=?")
        params.append(author)
    if company:
        logins = [r[0] for r in conn.execute("SELECT login FROM person WHERE company=?", (company,))]
        if not logins:
            where.append("1=0")
        else:
            where.append("author_login IN (%s)" % ",".join("?" * len(logins)))
            params += logins
    if classification and classification != "all":
        where.append("classification=?")
        params.append(classification)
    if flag in _DRILL_FLAGS:
        where.append(f"{flag}=1")
    if commit_type:
        where.append("commit_type=?")
        params.append(commit_type)
    if spec in ("0", "1"):
        where.append(f"is_spec={int(spec)}")
    if ai_tool:
        where.append("(',' || IFNULL(ai_tools,'') || ',') LIKE ?")
        params.append(f"%,{ai_tool},%")
    if pr_state == "merged":
        where.append("(merged_at IS NOT NULL AND merged_at<>'' OR UPPER(state)='MERGED')")
    elif pr_state == "abandoned":
        where.append("UPPER(state)='CLOSED' AND (merged_at IS NULL OR merged_at='')")
    elif pr_state == "open":
        where.append("UPPER(state)='OPEN'")
    elif pr_state == "open_unreviewed":
        # review_count, not the review table: in_flight() counts this column, and a
        # drill that disagreed with the tile it opened would be worse than no drill.
        where.append("UPPER(state)='OPEN' AND IFNULL(review_count,0)=0")
    if reviewed == "1" and entity == "pr":
        # a PR that got ≥1 review event — matches the Code-review coverage tile
        where.append("EXISTS (SELECT 1 FROM review rv WHERE rv.repo=pull_request.repo "
                     "AND rv.pr_number=pull_request.number AND rv.reviewer_login<>'')")
    if abandon_reason and entity == "pr":
        # Same CASE as abandoned_prs(), same "last closed event" rule — the actor is
        # read through a correlated subquery here instead of a CTE join so drill()'s
        # single-table query shape stays intact. item_type is in the predicate because
        # timeline_event's PK omits it.
        actor = ("(SELECT te.actor_login FROM timeline_event te "
                 "WHERE te.item_type='pull_request' AND te.repo=pull_request.repo "
                 "AND te.number=pull_request.number AND te.event='closed' "
                 "ORDER BY te.created_at DESC LIMIT 1)")
        where.append("UPPER(state)='CLOSED' AND (merged_at IS NULL OR merged_at='')")
        where.append("(%s) = ?" % _abandon_reason_sql("pull_request", actor).strip())
        params.append(abandon_reason)
    wsql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM {d['table']} WHERE {wsql}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT {d['cols']} FROM {d['table']} WHERE {wsql} "
        f"ORDER BY {d['date']} DESC LIMIT ? OFFSET ?", params + [limit, max(0, offset)]).fetchall()
    out = []
    for r in rows:
        repo, ref = r["repo"], r[d["ref"]]
        row = {"repo": repo, "ref": str(ref), "author": r["author_login"],
               "date": (r[d["date"]] or "")[:10], "title": (r["title"] or "").strip(),
               "url": f"https://github.com/{repo}/{d['kind']}/{ref}"}
        if entity == "commit":
            row["short"] = str(ref)[:8]
            row["meta"] = (f"+{r['meaningful_additions'] or 0}"
                           + (" · spec" if r["is_spec"] else "")
                           + (" · AI" if r["ai_marked"] else "")
                           + (f" · {r['commit_type']}" if r["commit_type"] else ""))
        elif entity == "pr":
            state = (r["state"] or ("MERGED" if r["merged_at"] else "")).lower()
            row["short"] = f"#{ref}"
            ttm = None
            if r["merged_at"]:
                try:
                    t0 = datetime.fromisoformat((r["created_at"] or "").replace("Z", "+00:00"))
                    t1 = datetime.fromisoformat(r["merged_at"].replace("Z", "+00:00"))
                    hh = (t1 - t0).total_seconds() / 3600.0
                    ttm = round(hh, 1) if hh >= 0 else None
                except (ValueError, AttributeError):
                    ttm = None
            row["ttm_h"] = ttm
            ttm_lbl = None
            if ttm is not None:
                ttm_lbl = (f"{round(ttm / 24, 1)}d to merge" if ttm >= 48
                           else f"{ttm}h to merge")
            row["meta"] = " · ".join(x for x in [
                state, ttm_lbl or "",
                f"+{r['additions']}" if r["additions"] is not None else "",
                f"{r['changed_files']} files" if r["changed_files"] is not None else ""] if x)
        else:
            kinds = [k for k, on in (("bug", r["is_bug"]), ("epic", r["is_epic"]),
                                     ("feature", r["is_feature"])) if on]
            row["short"] = f"#{ref}"
            row["meta"] = " · ".join(x for x in [(r["state"] or "").lower(),
                                     r["issue_type"] or "", " ".join(kinds)] if x)
        out.append(row)
    return {"entity": entity, "total": total, "shown": len(out),
            "capped": total > len(out), "rows": out}


def people_drill(conn, since: str, until: str, repos=None, company: str = "",
                 member_only: bool = False, limit: int = 500, offset: int = 0) -> dict:
    """The people behind a 'people' count: distinct contributors active in the window
    (and optional slice / company / members-only), each with their windowed activity.
    Reuses aggregate() so the count matches the tile exactly. entity='people' — the
    modal renders these as a person list (each links to their Person page), not commits."""
    agg = aggregate(conn, since, until, member_only=member_only, repos=repos)
    rows = agg["people"]
    # match each tile's definition of "a person":
    #  · element People = members with COMMITS in the element (member_only set)
    #  · company People = anyone with commits/PRs/specs/bugs/features (excludes issue-only)
    #  · active-people KPI = any activity at all (the full people set) — no extra filter
    if member_only:
        rows = [r for r in rows if r["commits"] > 0]
    elif company:
        rows = [r for r in rows
                if r["commits"] + r["prs"] + r["specs"] + r["bugs"] + r["features"] > 0]
    if company:
        rows = [r for r in rows if (r.get("company") or "Other") == company]
    total = len(rows)
    out = [{"login": r["login"], "name": r["name"] or r["login"],
            "company": r["company"], "is_member": r["is_member"],
            "commits": r["commits"], "prs": r["prs"], "specs": r["specs"],
            "bugs": r["bugs"], "epics": r.get("epics", 0), "features": r["features"]}
           for r in rows[max(0, offset):max(0, offset) + limit]]
    return {"entity": "people", "total": total, "shown": len(out),
            "capped": total > len(out), "rows": out}


def contributors_timeseries(conn: sqlite3.Connection, dates: list[str]) -> list[dict]:
    """CUMULATIVE distinct contributor count as of each date in `dates`.
    A contributor = a login with any commit / PR / issue by that date (bots and
    migration stubs excluded). Returns [{date, total, by_company:{co:n}}]."""
    comp_of = {r["login"]: (r["company"] or "Other")
               for r in conn.execute("SELECT login, company FROM person")}
    out = []
    for T in dates:
        rows = conn.execute("""
            SELECT author_login FROM commits
              WHERE is_bot=0 AND author_login<>'' AND committed_at<=?
            UNION SELECT author_login FROM pull_request
              WHERE is_bot=0 AND is_migration=0 AND author_login<>'' AND created_at<=?
            UNION SELECT author_login FROM issue
              WHERE is_bot=0 AND is_migration=0 AND author_login<>'' AND created_at<=?
        """, (T, T, T)).fetchall()
        logins = {r[0] for r in rows}
        by: dict = {}
        for l in logins:
            co = comp_of.get(l, "Other")
            by[co] = by.get(co, 0) + 1
        out.append({"date": T[:10], "total": len(logins), "by_company": by})
    return out


# --- export DB -> JSONL (git-committable, diffable backup of the binary DB) -
def person_weekly(conn: sqlite3.Connection, login: str, since: str, until: str,
                  max_repos: int = 8) -> dict:
    """Per-week activity for ONE author over [since, until]: commits + git line
    diff (additions/deletions) per repo, plus GitHub issues opened that week.
    Repo columns = the author's top repos by commit count (the rest folded into
    '(other)'). Weeks are Monday-start buckets spanning the range; empty weeks are
    kept (shown as blank rows, like the reference view)."""
    MAX_WEEKS = 60
    def monday(d: date) -> date:
        return d - timedelta(days=d.weekday())
    start = monday(date.fromisoformat(since[:10]))
    end = date.fromisoformat(until[:10])
    # cap very long spans (e.g. all-time) to the most recent MAX_WEEKS so the
    # table stays readable AND totals match the shown range
    cap_start = monday(end) - timedelta(weeks=MAX_WEEKS - 1)
    if start < cap_start:
        start = cap_start
    q_since = start.isoformat() + "T00:00:00Z"
    weeks: list[date] = []
    w = start
    while w <= end:
        weeks.append(w); w += timedelta(days=7)
    widx = {wk.isoformat(): i for i, wk in enumerate(weeks)}

    def wkey(iso: str) -> str:
        return monday(date.fromisoformat(iso[:10])).isoformat()

    crows = conn.execute(
        "SELECT repo, committed_at, additions, deletions FROM commits "
        "WHERE author_login=? AND committed_at>=? AND committed_at<=?",
        (login, q_since, until)).fetchall()
    repo_ct: dict = {}
    for r in crows:
        repo_ct[r["repo"]] = repo_ct.get(r["repo"], 0) + 1
    top = [rp for rp, _ in sorted(repo_ct.items(), key=lambda kv: -kv[1])[:max_repos]]
    topset = set(top)
    cols = top + (["(other)"] if len(repo_ct) > len(top) else [])

    cells: dict = {}
    for r in crows:
        wi = widx.get(wkey(r["committed_at"]))
        if wi is None:
            continue
        col = r["repo"] if r["repo"] in topset else "(other)"
        c = cells.setdefault((wi, col), {"commits": 0, "add": 0, "del": 0})
        c["commits"] += 1; c["add"] += r["additions"] or 0; c["del"] += r["deletions"] or 0

    issues = [0] * len(weeks)
    for r in conn.execute(
        "SELECT created_at FROM issue WHERE author_login=? AND is_bot=0 "
        "AND is_migration=0 AND created_at>=? AND created_at<=?", (login, q_since, until)):
        wi = widx.get(wkey(r["created_at"]))
        if wi is not None:
            issues[wi] += 1

    columns = [{"repo": c, "name": c.split("/")[-1] if "/" in c else c} for c in cols]
    rows = []
    for i, wk in enumerate(weeks):
        rows.append({"week": wk.isoformat(),
                     "week_end": (wk + timedelta(days=6)).isoformat(),
                     "cells": [cells.get((i, c)) for c in cols],
                     "issues": issues[i]})
    col_totals = [{"commits": sum(cells[(i, c)]["commits"] for i in range(len(weeks)) if (i, c) in cells),
                   "add": sum(cells[(i, c)]["add"] for i in range(len(weeks)) if (i, c) in cells),
                   "del": sum(cells[(i, c)]["del"] for i in range(len(weeks)) if (i, c) in cells)}
                  for c in cols]
    grand = {"commits": sum(t["commits"] for t in col_totals),
             "add": sum(t["add"] for t in col_totals),
             "del": sum(t["del"] for t in col_totals),
             "issues": sum(issues)}
    return {"login": login, "columns": columns, "rows": rows,
            "col_totals": col_totals, "grand": grand,
            "since": start.isoformat(), "until": until[:10]}


def person_profile(conn: sqlite3.Connection, login: str, since: str, until: str,
                   top_n: int = 8) -> dict:
    """Full per-person WINDOWED profile for the Person dashboard: headline totals
    (+ org share & commit rank), and composition by repo / platform-app / element /
    conventional commit-type / code-vs-specs. All-time impact (surviving code,
    reviews) is layered on by the caller from the run blob — those are git-blame /
    cumulative and don't belong to a window."""
    a = (since, until)
    c = conn.execute(
        "SELECT COUNT(*) commits, IFNULL(SUM(meaningful_additions),0) loc, "
        "IFNULL(SUM(is_spec),0) specs, IFNULL(SUM(ai_marked),0) ai FROM commits "
        "WHERE author_login=? AND is_bot=0 AND committed_at>=? AND committed_at<=?",
        (login, since, until)).fetchone()
    pr = conn.execute(
        "SELECT COUNT(*) prs, SUM(CASE WHEN merged_at IS NOT NULL AND merged_at<>'' "
        "THEN 1 ELSE 0 END) merged FROM pull_request WHERE author_login=? AND is_bot=0 "
        "AND is_migration=0 AND created_at>=? AND created_at<=?",
        (login, since, until)).fetchone()
    iss = conn.execute(
        "SELECT COUNT(*) issues, IFNULL(SUM(is_bug),0) bugs, IFNULL(SUM(is_feature),0) "
        "features, IFNULL(SUM(is_epic),0) epics FROM issue WHERE author_login=? AND is_bot=0 "
        "AND is_migration=0 AND created_at>=? AND created_at<=?", (login, since, until)).fetchone()
    totals = {"commits": c["commits"], "meaningful_additions": c["loc"] or 0,
              "specs": c["specs"] or 0, "ai_commits": c["ai"] or 0,
              "prs": pr["prs"], "prs_merged": pr["merged"] or 0,
              "issues": iss["issues"], "bugs": iss["bugs"] or 0,
              "features": iss["features"] or 0, "epics": iss["epics"] or 0}

    # org window totals + commit rank, for share % and standing among all people
    org = conn.execute(
        "SELECT COUNT(*) commits, IFNULL(SUM(meaningful_additions),0) loc, "
        "IFNULL(SUM(is_spec),0) specs FROM commits WHERE is_bot=0 AND author_login<>'' "
        "AND committed_at>=? AND committed_at<=?", a).fetchone()
    org_prs = conn.execute(
        "SELECT COUNT(*) prs FROM pull_request WHERE is_bot=0 AND is_migration=0 "
        "AND author_login<>'' AND created_at>=? AND created_at<=?", a).fetchone()["prs"]
    ranks = [r["author_login"] for r in conn.execute(
        "SELECT author_login, COUNT(*) n FROM commits WHERE is_bot=0 AND author_login<>'' "
        "AND committed_at>=? AND committed_at<=? GROUP BY author_login ORDER BY n DESC", a)]
    org_totals = {"commits": org["commits"], "meaningful_additions": org["loc"] or 0,
                  "specs": org["specs"] or 0, "prs": org_prs}
    shares = {k: _pct(totals.get(k, 0), org_totals.get(k, 0))
              for k in ("commits", "meaningful_additions", "specs", "prs")}

    # repo composition (top N by commits; rest folded into '(other)')
    repo_rows = conn.execute(
        'SELECT repo, COUNT(*) commits, IFNULL(SUM(additions),0) "add", '
        'IFNULL(SUM(deletions),0) "del" FROM commits WHERE author_login=? AND is_bot=0 '
        "AND committed_at>=? AND committed_at<=? GROUP BY repo ORDER BY commits DESC",
        (login, since, until)).fetchall()
    repos = [{"repo": r["repo"], "name": r["repo"].split("/")[-1],
              "commits": r["commits"], "add": r["add"], "del": r["del"]} for r in repo_rows]
    if len(repos) > top_n:
        rest = repos[top_n:]
        repos = repos[:top_n] + [{"repo": "(other)", "name": f"(+{len(rest)} more)",
                                  "commits": sum(x["commits"] for x in rest),
                                  "add": sum(x["add"] for x in rest),
                                  "del": sum(x["del"] for x in rest)}]

    by_cls: dict = {}
    for r in conn.execute(
        "SELECT classification k, COUNT(*) n FROM commits WHERE author_login=? AND is_bot=0 "
        "AND committed_at>=? AND committed_at<=? GROUP BY classification", (login, since, until)):
        if r["k"]:
            by_cls[r["k"]] = r["n"]
    _pmeta = _repo_type_meta()
    _pids = {m[0] for m in _pmeta}
    _pextra = sorted(c for c in by_cls if c not in _pids and c not in ("ignore", "unclassified"))
    _pordered = list(_pmeta) + [(c, c.replace("-", " ").replace("_", " ").title(), None) for c in _pextra]
    _ppal = tokens.CATEGORY_SWATCHES
    split_types = [{"id": tid, "name": tn, "color": tc or _ppal[i % len(_ppal)], "commits": by_cls.get(tid, 0)}
                   for i, (tid, tn, tc) in enumerate(_pordered)]
    split = {"types": split_types, "total": sum(t["commits"] for t in split_types)}

    elements = [{"element": r["el"], "commits": r["n"], "loc": r["loc"] or 0} for r in conn.execute(
        "SELECT rr.element el, COUNT(*) n, IFNULL(SUM(c.meaningful_additions),0) loc "
        "FROM commits c JOIN repo rr ON rr.key=c.repo WHERE c.author_login=? AND c.is_bot=0 "
        "AND rr.element IS NOT NULL AND rr.element<>'' AND c.committed_at>=? AND c.committed_at<=? "
        "GROUP BY rr.element ORDER BY n DESC", (login, since, until))]

    work_type = [{"type": (r["t"] or "other"), "count": r["n"]} for r in conn.execute(
        "SELECT commit_type t, COUNT(*) n FROM commits WHERE author_login=? AND is_bot=0 "
        "AND committed_at>=? AND committed_at<=? GROUP BY commit_type ORDER BY n DESC",
        (login, since, until))]

    specs = totals["specs"]
    code = max(totals["commits"] - specs, 0)
    mix = {"code": code, "specs": specs, "pct_code": _pct(code, totals["commits"]),
           "pct_specs": _pct(specs, totals["commits"])}

    # in-window activity sparkline (commits & LOC), same shape as store.aggregate
    spark_rows = conn.execute(
        "SELECT substr(committed_at,1,10) d, COUNT(*) n, "
        "IFNULL(SUM(meaningful_additions),0) loc FROM commits "
        "WHERE author_login=? AND is_bot=0 AND committed_at>=? AND committed_at<=? GROUP BY d",
        (login, since, until)).fetchall()
    bc, bl = _bucketize(since, until, {r["d"]: (r["n"], r["loc"] or 0) for r in spark_rows})
    spark = {"commits": bc, "loc": bl,
             "commits_pts": _spark_points(bc), "loc_pts": _spark_points(bl)}

    return {"login": login, "totals": totals, "org_totals": org_totals, "shares": shares,
            "rank": (ranks.index(login) + 1 if login in ranks else None),
            "n_people": len(ranks), "repos": repos, "split": split,
            "elements": elements, "work_type": work_type, "mix": mix, "spark": spark}


def person_totals(conn: sqlite3.Connection, login: str, since: str, until: str) -> dict:
    """Just this person's windowed KPI totals — cheap, for period-over-period deltas
    (mirrors the keys the Person dashboard's KPI tiles display)."""
    c = conn.execute(
        "SELECT COUNT(*) commits, IFNULL(SUM(meaningful_additions),0) loc, "
        "IFNULL(SUM(is_spec),0) specs FROM commits WHERE author_login=? AND is_bot=0 "
        "AND committed_at>=? AND committed_at<=?", (login, since, until)).fetchone()
    pr = conn.execute(
        "SELECT COUNT(*) prs, SUM(CASE WHEN merged_at IS NOT NULL AND merged_at<>'' "
        "THEN 1 ELSE 0 END) merged FROM pull_request WHERE author_login=? AND is_bot=0 "
        "AND is_migration=0 AND created_at>=? AND created_at<=?", (login, since, until)).fetchone()
    iss = conn.execute(
        "SELECT IFNULL(SUM(is_bug),0) bugs, IFNULL(SUM(is_feature),0) features, "
        "IFNULL(SUM(is_epic),0) epics FROM issue "
        "WHERE author_login=? AND is_bot=0 AND is_migration=0 AND created_at>=? AND created_at<=?",
        (login, since, until)).fetchone()
    return {"commits": c["commits"], "meaningful_additions": c["loc"] or 0,
            "specs": c["specs"] or 0, "prs": pr["prs"], "prs_merged": pr["merged"] or 0,
            "bugs": iss["bugs"] or 0, "features": iss["features"] or 0, "epics": iss["epics"] or 0}


# --- Developer score (v0, EXPERIMENTAL) --------------------------------------
# A compound per-person score: normalise each signal to a percentile WITHIN the
# active people for the window, average the percentiles inside four pillars, then
# take a weighted mean. Deliberately transparent (no ML) and directional — every
# input is visible and drills through. Pillar weights are tunable on the Calibrate
# page (next to the ground-truth labels and the backtest that suggests them).
#
# Design choices worth keeping honest:
#  * Percentile (relative) not absolute — the score describes standing in THIS
#    team/window, so it moves as the team moves.
#  * Volume/participation is the SMALLEST pillar; "how well" (delivery / craft)
#    outweighs "how much", per the product intent.
#  * Only signals that are genuinely per-person and available today. No revert/
#    reopen "blame" (semantically ambiguous per author) and no code-complexity
#    (we have no cyclomatic signal) — those are flagged as future work, not faked.
#  * A person needs a minimum of activity to be scored at all (else percentile
#    noise dominates); below it they're excluded from the ranking and the base.
#  * v0.2: Throughput + Collaboration were ~0.86 correlated in the calibration
#    backtest (double-counting volume), so they're collapsed into one "engagement"
#    axis — commits/LOC/PRs plus reviews-given/specs.
_SCORE_WEIGHTS = {"engagement": 20, "delivery": 25, "craft": 25, "flow": 35}
_SCORE_MIN_ACTIVITY = 5          # commits + PRs opened in the window to be scored
# A pillar is SCORED (missing→0 for an individual, a real minus) only when we have
# data for it across at least this fraction of the scored team. Below it, the pillar
# is a data-collection gap (e.g. flow before board snapshots accumulate), not a
# per-person shortfall, so it's dropped for everyone rather than tanking scores.
_SCORE_PILLAR_COVERAGE = 0.5


def _score_weights() -> dict:
    """Effective pillar weights: config.yaml `developer_score_weights` merged with
    the Config overlay, falling back to the built-in defaults. Non-negative; if the
    whole set is empty/zero we revert to defaults so scoring never divides by zero."""
    try:
        import configstore
        cfg = configstore.apply_overlay(configstore.base_config(), configstore.load_overlay())
        w = cfg.get("developer_score_weights") or {}
        out = {}
        for k in _SCORE_WEIGHTS:
            try:
                out[k] = max(0.0, float(w[k])) if k in w else float(_SCORE_WEIGHTS[k])
            except (TypeError, ValueError):
                out[k] = float(_SCORE_WEIGHTS[k])
        return out if sum(out.values()) > 0 else {k: float(v) for k, v in _SCORE_WEIGHTS.items()}
    except Exception:                # noqa: BLE001 — config is optional
        return {k: float(v) for k, v in _SCORE_WEIGHTS.items()}
# (pillar, metric-key, direction): +1 higher-is-better, -1 lower-is-better
_SCORE_SIGNALS = [
    ("engagement", "commits", 1), ("engagement", "loc", 1), ("engagement", "prs_merged", 1),
    ("engagement", "reviews_given", 1), ("engagement", "specs", 1),
    ("delivery", "ttm", -1), ("delivery", "size", -1),
    ("craft", "rounds", -1), ("craft", "merge_rate", 1),
    ("flow", "flow", -1),       # flow FRICTION per item (lower is better); see person_flow
]
# the ONE headline metric per pillar used to explain a rank gap in real terms
# ("you merge in 40h, they in 9h"). key → driver field, label, whether lower is better.
_PILLAR_PRIMARY = {
    "engagement": {"key": "commits", "label": "commits", "lower_better": False},
    "delivery":   {"key": "ttm", "label": "median merge time", "lower_better": True},
    "craft":      {"key": "rounds", "label": "review rounds/PR", "lower_better": True},
    "flow":       {"key": "flow", "label": "friction/item", "lower_better": True},
}
# How each signal is NAMED and PRINTED. Split from _SCORE_SIGNALS above so the tuple
# stays the machine-readable definition, but kept next to it because they have to
# describe the same ten keys — tests/test_developer_score.py asserts exactly that.
#
# This exists so the UI does not carry its own copy. A client that hardcodes "rounds is
# lower-is-better" drifts the first time the model changes, and the direction is not
# recoverable from anywhere else: the metric registry (_m) has no direction field, so
# _SCORE_SIGNALS is the only place that knows, and score_signal_spec() is how it travels.
# `fmt` is a hint, not a format string — the client owns rendering:
#   int    plain integer, thousands-separated
#   f1/f2  one / two decimals
#   hours  one decimal with an h suffix
#   pct01  a 0..1 ratio printed as a percentage
_SCORE_SIGNAL_META = {
    "commits":       ("Commits", "int"),
    "loc":           ("Meaningful LOC", "int"),
    "prs_merged":    ("PRs merged", "int"),
    "reviews_given": ("Reviews given", "int"),
    "specs":         ("Spec edits", "int"),
    "ttm":           ("Median merge time", "hours"),
    "size":          ("PR size", "f1"),
    "rounds":        ("Review rounds per PR", "f2"),
    "merge_rate":    ("Merge rate", "pct01"),
    "flow":          ("Friction per item", "f3"),
}


def score_signal_spec() -> list[dict]:
    """The score's signals as the UI needs them: which pillar, which way is better, and
    how to name and print each one. Derived from _SCORE_SIGNALS so the two cannot disagree.

    Ordered by pillar (heaviest weight first) and, inside a pillar, by _SCORE_SIGNALS'
    own order, so the client can render without sorting and every person's page agrees."""
    order = sorted(_SCORE_WEIGHTS, key=lambda p: -_SCORE_WEIGHTS[p])
    out = []
    for pillar in order:
        for p, key, direction in _SCORE_SIGNALS:
            if p != pillar:
                continue
            label, fmt = _SCORE_SIGNAL_META[key]
            out.append({"pillar": pillar, "key": key, "label": label,
                        "fmt": fmt, "higher_is_better": direction > 0})
    return out


# The band scale, as DATA, ascending by floor. _score_band reads it, the client draws it
# (via score_band_spec), and the floors are configurable — see _score_band_floors.
#
# The floors were 45/60/75 until 2026-08-04, and they were far harsher than they looked.
# Measured over the people who actually get banded (full pillar coverage) on production:
# 41% of them fell under 45 on a one-year window and 40% on a ninety-day one, against 7%
# above 75. "Building" was the label for two fifths of the org. That is not a statement
# about the org: the score is a weighted mean of percentiles, so its median is 50 BY
# CONSTRUCTION, and a floor at 45 therefore sits near the 41st percentile by arithmetic.
# Excluding the partial-coverage rows changes nothing — the skew is the scale, not the gaps.
#
# 30/50/70 instead. The bottom band becomes a genuine tail (7-11% measured, not 41%), the
# top stays selective without vanishing (14-15%), and the mass sits in the two middle bands
# — the same shape a consumer credit score has, where being in the middle-upper band is the
# norm and so the label does not read as a verdict. The middle floor is exactly 50, which
# is the median by construction: that boundary documents itself.
_SCORE_BANDS = [
    (0,  "Building",   "weak"),
    (30, "Developing", "warn"),
    (50, "Solid",      "good"),
    (70, "Strong",     "good"),
]
# Where a suggested scale puts its outer floors, as quantiles of the scored population.
# The middle one is not here: it is pinned to the median, which the score defines as 50.
_BAND_SUGGEST_Q = (0.10, 0.85)


def _score_band_floors() -> dict:
    """Effective band floors as {band: floor}: config.yaml `developer_score_bands` merged
    with the Config overlay, falling back to the built-in scale.

    Guarded the way the scale has to be: the lowest band always starts at 0, floors must be
    strictly ascending, and every band must be present. A scale that is out of order or has
    a hole is not a milder scale, it is a broken one — _score_band walks it from the top and
    would hand back the wrong label — so anything invalid falls back whole rather than in
    part."""
    base = {b: lo for lo, b, _ in _SCORE_BANDS}
    try:
        import configstore
        cfg = configstore.apply_overlay(configstore.base_config(), configstore.load_overlay())
        raw = cfg.get("developer_score_bands") or {}
        out = dict(base)
        for b in base:
            if b in raw:
                out[b] = int(round(float(raw[b])))
        floors = [out[b] for lo, b, _ in _SCORE_BANDS]
        if floors[0] != 0 or any(a >= b for a, b in zip(floors, floors[1:])):
            return base
        return out
    except Exception:                # noqa: BLE001 — config is optional
        return base


def score_band_spec() -> list[dict]:
    """The band scale for a client that has to draw it: floor, label, tone, ascending."""
    floors = _score_band_floors()
    return [{"min": floors[b], "band": b, "tone": t} for _, b, t in _SCORE_BANDS]


def _score_band(s, spec=None):
    """The band for a score. `spec` lets a caller resolve the scale ONCE and reuse it:
    _score_band_floors reads the merged config, and developer_scores would otherwise pay
    that for the total plus every pillar of every person — five reads per row."""
    if s is None:
        return ("—", "na")
    for stop in reversed(spec if spec is not None else score_band_spec()):
        if s >= stop["min"]:
            return (stop["band"], stop["tone"])
    return (_SCORE_BANDS[0][1], _SCORE_BANDS[0][2])


def suggest_score_bands(sc: dict) -> dict | None:
    """Floors the CURRENT window's distribution would put the chosen shape at, as
    {band: floor} — a suggestion for the Calibrate page, never applied on its own.

    Suggests rather than sets, for the same reason the weights are suggested: pinning the
    floors to quantiles every window would make a person's LABEL move when the team moves,
    on top of the score already doing so. A human accepts a scale and then it holds still.

    Computed over people with FULL pillar coverage only, because those are the only rows
    that get banded — a missing pillar counts as zero, and letting those scores into the
    quantiles would drag the bottom floor down to accommodate a data gap. Returns None when
    there is too little to fit, which is not the same as a scale of zeros.

    Takes a developer_scores RESULT rather than a window, because it used to score the window
    itself — so the Calibrate page ran the full scorer three times for one request, over a
    2008-2099 span at that."""
    import statistics
    act = sc["active_pillars"]
    scores = sorted(r["score"] for r in sc["board"]
                    if all(r["pillars"].get(p) is not None for p in act))
    if len(scores) < 8:
        return None

    def q(p):
        i = (len(scores) - 1) * p
        lo, hi = int(i), min(int(i) + 1, len(scores) - 1)
        return scores[lo] + (scores[hi] - scores[lo]) * (i - lo)

    names = [b for _, b, _ in _SCORE_BANDS]
    mid = round(statistics.median(scores))          # 50 by construction; measured anyway
    out = {names[0]: 0, names[1]: round(q(_BAND_SUGGEST_Q[0])),
           names[2]: mid, names[3]: round(q(_BAND_SUGGEST_Q[1]))}
    floors = [out[b] for b in names]
    # A tight distribution can collapse two floors onto each other; nudge rather than
    # return a scale that _score_band cannot walk.
    for i in range(1, len(floors)):
        if floors[i] <= floors[i - 1]:
            floors[i] = floors[i - 1] + 1
    return dict(zip(names, floors))


def _median(xs):
    """Median of a list, or None when empty. Same convention as the local helper in
    trend_block: even-length lists average the two middle values."""
    xs = sorted(xs)
    m = len(xs)
    if not m:
        return None
    return xs[m // 2] if m % 2 else (xs[m // 2 - 1] + xs[m // 2]) / 2


#: Age buckets for open PRs. Deliberately bands rather than a mean: one 237-day
#: draft would drag an average into meaninglessness while hiding that it is a single
#: outlier. Upper bound is exclusive; the last band is open-ended.
IN_FLIGHT_BANDS = (("d7", "< 7 days", 0, 7), ("d30", "7–30 days", 7, 30),
                   ("d90", "30–90 days", 30, 90), ("d90p", "> 90 days", 90, None))

#: An open PR with no review after this many days is waiting on the team rather
#: than on its author. Chosen to be shorter than the 30-day "aging" cutoff: a week
#: without anyone looking is already a review-queue problem.
STALE_REVIEW_DAYS = 7


def in_flight(conn, repos=None) -> dict:
    """Work currently open, as of now — NOT period-scoped.

    Deliberately takes no since/until: "in flight" is a *now* quantity and must not
    move when the period control changes, so the signature makes that structural
    rather than a convention a later edit could quietly break. A repo slice still
    applies — slicing by element/repo is about *which* work, not *when*.

    This exists because only default-branch commits are collected, so a long-lived
    PR's work is invisible until it merges and its author reads as idle. It is a
    separate signal from delivered work and must never be summed into the commit /
    LOC counters — see docs/superpowers/plans/2026-07-28-work-in-flight.md.
    """
    rf, rp = _repo_filter(repos, "repo")
    rows = conn.execute(
        "SELECT repo, number, author_login, created_at, is_draft, title, "
        "       IFNULL(review_count,0) reviews, IFNULL(additions,0) additions, "
        "       IFNULL(changed_files,0) files, "
        "       julianday('now') - julianday(created_at) age "
        "FROM pull_request "
        "WHERE state='OPEN' AND is_bot=0 AND is_migration=0 AND created_at<>''"
        + rf + " ORDER BY age DESC", rp).fetchall()

    items = [{"repo": r["repo"], "number": r["number"], "login": r["author_login"] or "",
              "draft": bool(r["is_draft"]), "reviews": r["reviews"],
              "additions": r["additions"], "files": r["files"],
              "title": r["title"] or "", "age_d": int(r["age"] or 0)} for r in rows]

    ages = sorted(i["age_d"] for i in items)
    median = _median(ages)

    bands = []
    for key, label, lo, hi in IN_FLIGHT_BANDS:
        n = sum(1 for i in items
                if i["age_d"] >= lo and (hi is None or i["age_d"] < hi))
        bands.append({"key": key, "label": label, "n": n})

    # Phase 3 — the actionable end of aging WIP. An open PR nobody has reviewed is
    # waiting on the team, not on its author, so it is ranked separately and by wait
    # time.
    #
    # It would be better to separate "nobody responded" from "nobody was asked", but
    # that is not derivable: collect.py hardcodes review_requested_at to None, so the
    # column is empty on all 2,244 rows. Reporting "never asked" from it would always
    # read 100% — a misleading number rather than a missing one, so it is left out.
    stale_unreviewed = sorted(
        (i for i in items if not i["reviews"] and i["age_d"] >= STALE_REVIEW_DAYS),
        key=lambda i: -i["age_d"])

    # Phase 4 — size WITHOUT a total. A sum of additions is dominated by fork-sync PRs
    # (one PR was 35% of the org-wide total when this was measured), so the shape is
    # reported as median/p90 with the outliers named instead. These are RAW GitHub
    # line counts: unlike commits, PR diffs carry no meaningful-LOC filter, so vendored
    # and generated files are included. Hence "size shape", never "lines delivered".
    adds = sorted(i["additions"] for i in items)
    biggest = sorted(items, key=lambda i: -i["additions"])[:5]

    # Per person as its OWN list, not a column on Flow's period-scoped "By person"
    # table: that table only lists owners with items created in the window, so the
    # very person this feature is for — whose only activity is one long-lived PR —
    # would be absent from it entirely.
    by_login: dict = {}
    for i in items:
        if not i["login"]:
            continue
        p = by_login.setdefault(i["login"], {"login": i["login"], "n": 0, "drafts": 0,
                                             "unreviewed": 0, "oldest_age_d": 0})
        p["n"] += 1
        p["drafts"] += 1 if i["draft"] else 0
        p["unreviewed"] += 1 if not i["reviews"] else 0
        p["oldest_age_d"] = max(p["oldest_age_d"], i["age_d"])
    people = sorted(by_login.values(), key=lambda p: (-p["oldest_age_d"], -p["n"]))

    return {
        # Two separate facts the UI has to convey, and they are not the same:
        #  · period_scoped=False — the period control does not apply here at all.
        #  · the PR *set* is as of the last collect, while ages are measured from now.
        #    So a PR merged since the last refresh still shows as open. /health/data
        #    reports how stale that is; this panel should not pretend to be live.
        "period_scoped": False,
        # The cutoff behind the "open over 30 days" count, computed HERE so the tile
        # and its drill (which filters created_at <= this date) can never disagree by
        # a timezone or a clock skew between server and browser.
        "stale_before": (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d"),
        "stale_days": 30,
        "n": len(items),
        "drafts": sum(1 for i in items if i["draft"]),
        "unreviewed": sum(1 for i in items if not i["reviews"]),
        "median_age_d": median,
        "oldest_age_d": ages[-1] if ages else None,
        "bands": bands,
        "people": people,
        # Phase 3
        "stale_review_days": STALE_REVIEW_DAYS,
        "stale_unreviewed": stale_unreviewed[:10],
        "stale_unreviewed_n": len(stale_unreviewed),
        # Phase 4 — shape, not a sum (see above)
        "size": {"median_additions": _median(adds),
                 "p90_additions": (adds[min(len(adds) - 1, int(0.9 * len(adds)))]
                                   if adds else None),
                 "median_files": _median([i["files"] for i in items]),
                 "raw_lines": True,
                 "biggest": biggest},
        "items": items,
    }


_mreg.register_for(in_flight, [
    _m("in_flight_prs", type="direct", group="delivery", unit="count",
       desc="Pull requests open right now (bots and migration PRs excluded). NOT "
            "period-scoped — it is a point-in-time quantity and does not change with "
            "the selected period. Never added to commit or LOC counters.",
       formula="COUNT(pull_request) where state='OPEN', as of now",
       snippet="SELECT COUNT(*) FROM pull_request\n"
               "WHERE state='OPEN' AND is_bot=0 AND is_migration=0"),
    _m("in_flight_age_bands", type="computed", group="delivery", unit="count",
       desc="Open PRs bucketed by age (<7d / 7-30d / 30-90d / >90d). Bands rather "
            "than a mean so a single very old PR reads as an outlier instead of "
            "distorting the average.",
       formula="BUCKET open PRs BY (now - created_at)",
       snippet="SELECT julianday('now') - julianday(created_at) FROM pull_request\n"
               "WHERE state='OPEN' AND is_bot=0 AND is_migration=0"),
    _m("in_flight_unreviewed", type="computed", group="delivery", unit="count",
       desc="Open PRs with no review yet. Paired with age this is the actionable "
            "half of work-in-flight: nobody has looked, and for how long.",
       formula="COUNT(open PRs) where review_count=0",
       snippet="SELECT COUNT(*) FROM pull_request\n"
               "WHERE state='OPEN' AND IFNULL(review_count,0)=0 AND is_bot=0"),
    _m("in_flight_stale_unreviewed", type="computed", group="delivery", unit="count",
       desc=f"Open PRs with no review after {STALE_REVIEW_DAYS} days. These are "
            "waiting on the team rather than on their author, which is why they are "
            "ranked by wait time rather than counted. Note that 'asked for review but "
            "ignored' cannot be separated from 'never asked': collect.py never "
            "populates review_requested_at, so that column is empty on every row.",
       formula=f"open PRs where review_count=0 AND age >= {STALE_REVIEW_DAYS}d, by age desc",
       snippet="SELECT * FROM pull_request WHERE state='OPEN'\n"
               "  AND IFNULL(review_count,0)=0 ORDER BY created_at ASC"),
    _m("in_flight_size_shape", type="computed", group="code", unit="lines",
       desc="Median and p90 additions across open PRs, plus the five biggest by name. "
            "Deliberately NOT a total: when measured, one fork-sync PR accounted for "
            "35% of all open additions and the top three for 73%, so a sum describes "
            "the outliers rather than the work. These are RAW GitHub line counts — PR "
            "diffs carry no meaningful-LOC filter, so vendored and generated files are "
            "included, unlike the commit path.",
       formula="median / p90 over open PR additions; outliers listed, never summed",
       snippet="SELECT additions FROM pull_request WHERE state='OPEN' AND is_bot=0"),
])


#: Age-at-close buckets for abandoned PRs. Upper bound exclusive, last one open-ended.
ABANDON_AGE_BANDS = (("d1", "< 1 day", 0, 1), ("d7", "1–7 days", 1, 7),
                     ("d30", "8–30 days", 7, 30), ("d90", "31–90 days", 30, 90),
                     ("d90p", "> 90 days", 90, None))

#: The five reasons a PR ends up closed-unmerged, in the order they are displayed.
#: Derived, not collected: the closing actor is already stored on the `closed`
#: timeline event, and comparing it to the author (plus review_count and is_draft)
#: splits every abandoned PR into exactly one of these. `swept` is deliberately last
#: and named separately — it is the smallest bucket and the only real problem in the
#: set (see the plan's §5.2), because nobody ever looked at those PRs.
ABANDON_REASONS = (
    ("withdrawn_reviewed", "Withdrawn after review", "the author closed it after feedback"),
    ("withdrawn_unreviewed", "Withdrawn, never reviewed", "the author closed it before anyone looked"),
    ("draft", "Never finished", "still a draft when it was closed"),
    ("rejected", "Rejected after review", "somebody else closed it after review"),
    ("swept", "Closed by another, unreviewed", "nobody ever reviewed it"),
)

def _abandon_reason_sql(pr: str = "pr", actor: str = "lc.actor_login") -> str:
    """The CASE that assigns an abandonment reason, parameterised by table alias.

    ONE definition, used by both abandoned_prs() and drill(): if the aggregate and the
    drill behind it computed the bucket separately they could disagree, and a drill
    that contradicts the tile it opened is worse than no drill at all.
    """
    return f"""
  CASE
    WHEN {pr}.is_draft=1 THEN 'draft'
    WHEN {actor} IS NULL OR {actor}='' THEN 'unknown'
    WHEN LOWER({actor})=LOWER({pr}.author_login) AND IFNULL({pr}.review_count,0)>0
         THEN 'withdrawn_reviewed'
    WHEN LOWER({actor})=LOWER({pr}.author_login) THEN 'withdrawn_unreviewed'
    WHEN IFNULL({pr}.review_count,0)>0 THEN 'rejected'
    ELSE 'swept'
  END
"""

#: LAST closing actor per PR. There can be more than one `closed` event when a PR was
#: closed, reopened and closed again — joining them all reports more rows than PRs and
#: inflates every bucket. item_type is in the predicate because timeline_event's PK
#: omits it, so an issue #N could otherwise collide with PR #N.
_LAST_CLOSE_CTE = """
WITH last_close AS (
  SELECT repo, number, actor_login,
         ROW_NUMBER() OVER (PARTITION BY repo, number ORDER BY created_at DESC) rn
  FROM timeline_event
  WHERE item_type='pull_request' AND event='closed'
)
"""


def abandoned_prs(conn, since: str, until: str, repos=None) -> dict:
    """Closed-unmerged PRs in the window, with why they ended that way.

    Period-scoped by closed_at — unlike in_flight(), "abandoned in this window" is an
    honest question because closed_at exists on every such row. The abandon RATE is
    deliberately measured against PRs *closed* in the window (abandoned ÷ abandoned +
    merged), not against PRs opened in it: a window that happens to clear a merge
    backlog would otherwise read as a quality collapse.

    Not a waste metric. The dominant reason is authors withdrawing their own work
    after feedback, which is feedback working; the reason taxonomy exists so that is
    not averaged together with the one bucket that is a real problem — PRs nobody ever
    reviewed before somebody else closed them. See the plan's §5.2.
    """
    rf, rp = _repo_filter(repos, "pr.repo")
    rows = conn.execute(
        _LAST_CLOSE_CTE +
        "SELECT pr.repo, pr.number, pr.author_login, pr.created_at, pr.closed_at, "
        "       pr.is_draft, pr.title, IFNULL(pr.review_count,0) reviews, "
        "       IFNULL(pr.additions,0) additions, " + _abandon_reason_sql() + " reason, "
        "       julianday(pr.closed_at) - julianday(pr.created_at) lived "
        "FROM pull_request pr "
        "LEFT JOIN last_close lc ON lc.repo=pr.repo AND lc.number=pr.number AND lc.rn=1 "
        "WHERE UPPER(pr.state)='CLOSED' AND (pr.merged_at IS NULL OR pr.merged_at='') "
        "  AND pr.is_bot=0 AND pr.is_migration=0 "
        "  AND pr.closed_at>=? AND pr.closed_at<=?" + rf,
        (since, until) + rp).fetchall()

    items = [{"repo": r["repo"], "number": r["number"], "login": r["author_login"] or "",
              "reason": r["reason"], "reviews": r["reviews"], "draft": bool(r["is_draft"]),
              "additions": r["additions"], "title": r["title"] or "",
              "lived_d": int(r["lived"] or 0), "closed_at": r["closed_at"]}
             for r in rows]

    merged = conn.execute(
        "SELECT COUNT(*) n FROM pull_request pr "
        "WHERE pr.merged_at IS NOT NULL AND pr.merged_at<>'' AND pr.is_bot=0 "
        "  AND pr.is_migration=0 AND pr.merged_at>=? AND pr.merged_at<=?" + rf,
        (since, until) + rp).fetchone()["n"]

    n = len(items)
    closed_total = n + merged
    reasons = []
    for key, label, sub in ABANDON_REASONS:
        grp = [i for i in items if i["reason"] == key]
        reasons.append({
            "key": key, "label": label, "sub": sub, "n": len(grp),
            "reviews": sum(i["reviews"] for i in grp),
            "median_lived_d": _median([i["lived_d"] for i in grp]),
            "oldest_lived_d": max((i["lived_d"] for i in grp), default=None),
        })
    unknown = [i for i in items if i["reason"] == "unknown"]
    if unknown:                      # only when timeline coverage is incomplete
        reasons.append({"key": "unknown", "label": "Reason unknown",
                        "sub": "no closing event collected for these",
                        "n": len(unknown), "reviews": sum(i["reviews"] for i in unknown),
                        "median_lived_d": _median([i["lived_d"] for i in unknown]),
                        "oldest_lived_d": max((i["lived_d"] for i in unknown), default=None)})

    bands = [{"key": k, "label": lb,
              "n": sum(1 for i in items if i["lived_d"] >= lo and (hi is None or i["lived_d"] < hi))}
             for k, lb, lo, hi in ABANDON_AGE_BANDS]

    by_repo: dict = {}
    for i in items:
        d = by_repo.setdefault(i["repo"], {"repo": i["repo"], "n": 0, "reviews": 0, "swept": 0})
        d["n"] += 1
        d["reviews"] += i["reviews"]
        d["swept"] += 1 if i["reason"] == "swept" else 0
    repos_out = sorted(by_repo.values(), key=lambda d: (-d["n"], d["repo"]))

    # The headline list: never-reviewed PRs somebody else closed, longest-waiting
    # first. Small by count and invisible in any ranking that sorts by volume.
    swept = sorted((i for i in items if i["reason"] == "swept"),
                   key=lambda i: -i["lived_d"])

    return {
        "period_scoped": True,
        "n": n,
        "merged": merged,
        "closed_total": closed_total,
        "rate_pct": round(100 * n / closed_total, 1) if closed_total else None,
        "reviewed": sum(1 for i in items if i["reviews"]),
        "unreviewed": sum(1 for i in items if not i["reviews"]),
        "reviews_total": sum(i["reviews"] for i in items),
        "drafts": sum(1 for i in items if i["draft"]),
        "reasons": reasons,
        "bands": bands,
        "repos": repos_out,
        "swept": swept,
        "items": items,
    }


_mreg.register_for(abandoned_prs, [
    _m("abandoned_prs", type="direct", group="delivery", unit="count",
       desc="Pull requests closed without merging in the window (bots and "
            "migration PRs excluded). Windowed by closed_at.",
       formula="COUNT(pull_request) where state='CLOSED', no merged_at, closed_at in window",
       snippet="SELECT COUNT(*) FROM pull_request\n"
               "WHERE UPPER(state)='CLOSED' AND (merged_at IS NULL OR merged_at='')\n"
               "  AND closed_at BETWEEN ? AND ?"),
    _m("abandon_rate", type="computed", group="delivery", unit="%",
       desc="Share of PRs CLOSED in the window that were abandoned rather than "
            "merged. Measured against closures, not against PRs opened in the window, "
            "so clearing a merge backlog cannot masquerade as a quality collapse.",
       formula="abandoned / (abandoned + merged), both by their own close/merge date",
       snippet="abandoned = COUNT(*) closed-unmerged in window\n"
               "merged    = COUNT(*) with merged_at in window"),
    _m("abandon_reasons", type="computed", group="delivery", unit="count",
       desc="Why a PR was abandoned: withdrawn after review / withdrawn unreviewed / "
            "never finished (draft) / rejected after review / closed by another with "
            "no review. Derived from the closing actor already stored on the 'closed' "
            "timeline event compared with the PR author, plus review_count and "
            "is_draft — no extra collection. Deliberately NOT summed into one number: "
            "an author withdrawing after feedback is feedback working, while a PR "
            "nobody reviewed before someone swept it up is a real problem.",
       formula="CASE on (last closing actor vs author, review_count, is_draft)",
       snippet="LEFT JOIN the LAST 'closed' timeline_event per PR (a reopened-then-\n"
               "reclosed PR has several) and compare actor_login with author_login"),
])


# A commit that shows up more than this long after its own author date moved a window
# that may already have been reported. One day rather than zero, because a nightly
# collect legitimately picks up yesterday's work, and because squash merges land within
# an hour of the merge (measured p50 = 0.0h over 246 matched commits) — under a day is
# normal operation, not back-dating.
BACKDATE_DAYS = 1


def backdating_stats(conn, repos=None, days: float = BACKDATE_DAYS) -> dict:
    """How far commits actually arrive behind their own author date, per repo.

    Every windowed query filters on committed_at, which is the git AUTHOR date, so a
    long-lived PR merging on a merge- or rebase-strategy repo pushes its commits back
    into windows that were already reported — §1 of
    docs/superpowers/plans/2026-07-28-work-in-flight.md. `commits.first_seen` records when
    each row actually entered this DB, which is what turns that from an argument into a
    measurement. This function only reports the size of the problem; it changes no window
    and no existing metric.

    Per-repo rather than one org-wide number, because the exposure is very uneven.
    Measured on prod before the column existed: squash merges do NOT back-date (95% of
    squash commits carry an author date within an hour of merged_at), so repos that never
    squash — example-app, studio, example-core — carry nearly all of it, while the partly
    squashing ones are partly protected. An org median would average that away.

    COVERAGE IS THE CAVEAT, and it is reported rather than hidden: only rows written since
    first_seen existed carry a stamp. Older rows read NULL, land in `unknown`, and are
    kept out of every percentage — a NULL means "was already here", never "arrived at time
    zero". So a DB that has not collected since the migration honestly reports measured=0
    instead of a reassuring 0% back-dating. Rows whose committed_at is unparseable also
    fall into `unknown`, since no lag can be computed for them either.
    """
    rf, rp = _repo_filter(repos, "repo")
    rows = conn.execute(
        "SELECT repo, CASE WHEN IFNULL(first_seen,'')='' THEN NULL "
        "  ELSE (julianday(first_seen) - julianday(committed_at)) * 24.0 END lag_h "
        "FROM commits WHERE IFNULL(committed_at,'')<>''" + rf, rp).fetchall()

    cutoff_h = days * 24.0
    lags: list = []
    unknown = 0
    per: dict = {}
    for r in rows:
        d = per.setdefault(r["repo"], {"repo": r["repo"], "measured": 0, "unknown": 0,
                                       "backdated": 0, "_lags": []})
        if r["lag_h"] is None:
            unknown += 1
            d["unknown"] += 1
            continue
        lags.append(r["lag_h"])
        d["_lags"].append(r["lag_h"])
        d["measured"] += 1
        if r["lag_h"] > cutoff_h:
            d["backdated"] += 1

    def _p90(xs):                        # same convention as in_flight's size shape
        return round(xs[min(len(xs) - 1, int(0.9 * len(xs)))], 1) if xs else None

    repo_rows = []
    for d in per.values():
        ls = sorted(d.pop("_lags"))
        d["backdated_pct"] = _pct(d["backdated"], d["measured"])
        d["median_lag_h"] = round(_median(ls), 1) if ls else None
        d["p90_lag_h"] = _p90(ls)
        d["max_lag_h"] = round(ls[-1], 1) if ls else None
        repo_rows.append(d)
    repo_rows.sort(key=lambda d: (-d["backdated"], -d["measured"], d["repo"]))

    srt = sorted(lags)
    measured = len(srt)
    return {
        # Not period-scoped: the question is how much the windows move, which is a
        # property of the collected history as a whole, not of any one window.
        "period_scoped": False,
        "days": days,
        "measured": measured,
        "unknown": unknown,
        "coverage_pct": _pct(measured, measured + unknown),
        "backdated": sum(1 for x in srt if x > cutoff_h),
        "backdated_pct": _pct(sum(1 for x in srt if x > cutoff_h), measured),
        "median_lag_h": round(_median(srt), 1) if srt else None,
        "p90_lag_h": _p90(srt),
        "max_lag_h": round(srt[-1], 1) if srt else None,
        "repos": repo_rows,
    }


_mreg.register_for(backdating_stats, [
    _m("commit_backdating", type="computed", group="quality", unit="count",
       desc=f"Commits that entered the database more than {BACKDATE_DAYS} day(s) after "
            "their own git author date — i.e. work that landed in a window which had "
            "already been reported, because every window filters on the author date. "
            "ONLY counts rows collected since commits.first_seen was introduced; rows "
            "written before that read NULL and are reported separately as 'unknown', "
            "never as zero. Reported per repo because the exposure is uneven: squash "
            "merges do not back-date (95% land within an hour of the merge), so repos "
            "that never squash carry almost all of it. Diagnostic only — it does not "
            "change any window or any other metric.",
       formula=f"COUNT(commits) where first_seen - committed_at > {BACKDATE_DAYS}d, "
               "over rows with a non-NULL first_seen",
       snippet="SELECT repo, COUNT(*) FROM commits\n"
               "WHERE first_seen IS NOT NULL\n"
               "  AND julianday(first_seen) - julianday(committed_at) > 1\n"
               "GROUP BY repo"),
    _m("commit_backdating_lag", type="computed", group="quality", unit="hours",
       desc="Median and p90 of (first_seen - committed_at) — how late commits arrive "
            "relative to the date they are counted on. Median and p90 rather than a mean "
            "so one PR that sat open for months reads as an outlier instead of moving the "
            "headline. Same coverage caveat: computed only over rows that have a "
            "first_seen stamp, with the unstamped count reported alongside so a low "
            "figure cannot be mistaken for a healthy one on a DB that simply has not "
            "collected yet.",
       formula="median / p90 over (first_seen - committed_at) in hours, non-NULL only",
       snippet="SELECT (julianday(first_seen) - julianday(committed_at)) * 24.0\n"
               "FROM commits WHERE first_seen IS NOT NULL ORDER BY 1"),
])


def developer_scores(conn, since: str, until: str, repos=None) -> dict:
    """EXPERIMENTAL v0 compound score per person for the window (+ optional slice).
    Returns weights, the eligibility floor, and a `board` (ranked, each with pillar
    sub-scores + the raw drivers) plus a `by_login` lookup. See notes above."""
    import bisect
    weights = _score_weights()
    rf, rp = _repo_filter(repos)
    prf, prp = _repo_filter(repos, "repo")

    def blank():
        return {"commits": 0, "loc": 0, "specs": 0, "ai": 0, "prs_opened": 0,
                "prs_merged": 0, "_ttms": [], "_sizes": [], "_rounds": [], "reviews_given": 0}
    raw: dict = {}
    for r in conn.execute(
        "SELECT author_login lg, COUNT(*) commits, IFNULL(SUM(meaningful_additions),0) loc, "
        "IFNULL(SUM(is_spec),0) specs, IFNULL(SUM(ai_marked),0) ai FROM commits c "
        "WHERE is_bot=0 AND author_login<>'' AND committed_at>=? AND committed_at<=?"
        + rf + " GROUP BY author_login", (since, until) + rp):
        e = raw.setdefault(r["lg"], blank())
        e.update(commits=r["commits"], loc=r["loc"], specs=r["specs"], ai=r["ai"])
    for r in conn.execute(
        "SELECT author_login lg, created_at, merged_at, changed_files, review_count, is_revert "
        "FROM pull_request WHERE is_bot=0 AND is_migration=0 AND author_login<>'' "
        "AND created_at>=? AND created_at<=?" + prf, (since, until) + prp):
        e = raw.setdefault(r["lg"], blank())
        e["prs_opened"] += 1
        if r["changed_files"] is not None:
            e["_sizes"].append(r["changed_files"])
        if r["merged_at"]:
            e["prs_merged"] += 1
            if r["review_count"] is not None:
                e["_rounds"].append(r["review_count"])
            try:
                d0 = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
                d1 = datetime.fromisoformat(r["merged_at"].replace("Z", "+00:00"))
                h = (d1 - d0).total_seconds() / 3600.0
                if h >= 0:
                    e["_ttms"].append(h)
            except (ValueError, AttributeError):
                pass
    for r in conn.execute(
        "SELECT reviewer_login lg, COUNT(*) n FROM review WHERE reviewer_login IS NOT NULL "
        "AND reviewer_login<>'' AND submitted_at>=? AND submitted_at<=?"
        + prf + " GROUP BY reviewer_login", (since, until) + prp):
        if r["lg"] in raw:
            raw[r["lg"]]["reviews_given"] = r["n"]

    names = {r["login"]: (r["name"] or r["login"])
             for r in conn.execute("SELECT login, name FROM person")}

    # flow-efficiency (taxonomy): forward-flow ratio of each person's board items,
    # from the Projects-v2 snapshots. Independent axis; None where we lack enough
    # tracked movement (it strengthens as snapshots accumulate).
    try:
        import semantic_metrics
        flow = semantic_metrics.person_flow(conn, repos, since, until)
    except Exception:                # noqa: BLE001 — taxonomy/flow is optional
        flow = {}

    # derive per-person metrics (medians / rates) from the collected rows
    for lg, e in raw.items():
        e["ttm"] = statistics.median(e["_ttms"]) if e["_ttms"] else None
        e["size"] = statistics.median(e["_sizes"]) if e["_sizes"] else None
        e["rounds"] = (sum(e["_rounds"]) / len(e["_rounds"])) if e["_rounds"] else None
        e["merge_rate"] = (e["prs_merged"] / e["prs_opened"]) if e["prs_opened"] else None
        e["ai_share"] = (100.0 * e["ai"] / e["commits"]) if e["commits"] else 0.0
        e["activity"] = e["commits"] + e["prs_opened"]
        e["flow"] = flow.get(lg)

    eligible = [lg for lg, e in raw.items() if e["activity"] >= _SCORE_MIN_ACTIVITY]

    # per-signal sorted value lists over eligible people (for percentile ranking)
    dists = {}
    for _, key, _dir in _SCORE_SIGNALS:
        dists[key] = sorted(raw[lg][key] for lg in eligible if raw[lg].get(key) is not None)

    def pctl(key, v, direction):
        vals = dists[key]
        n = len(vals)
        if n <= 1:
            return 0.5
        below = bisect.bisect_left(vals, v)
        equal = bisect.bisect_right(vals, v) - below
        p = (below + 0.5 * equal) / n
        return p if direction > 0 else (1.0 - p)

    # per-person pillar percentiles (None where the pillar has no signal for them)
    per_pillars = {}
    for lg in eligible:
        e = raw[lg]
        buckets: dict = {}
        for pillar, key, direction in _SCORE_SIGNALS:
            v = e.get(key)
            if v is not None:
                buckets.setdefault(pillar, []).append(pctl(key, v, direction))
        per_pillars[lg] = {p: (round(100 * sum(buckets[p]) / len(buckets[p]))
                               if p in buckets else None) for p in _SCORE_WEIGHTS}

    # which pillars are SCORED this window: those with data across ≥ coverage of the
    # team (engagement is always in). A pillar missing for ~everyone is a data gap,
    # dropped for all; a pillar present for the team but missing for ONE person
    # counts as 0 for them — "didn't ship / didn't review" is a minus, not a free pass.
    n_elig = len(eligible) or 1
    coverage = {p: sum(1 for lg in eligible if per_pillars[lg][p] is not None) / n_elig
                for p in _SCORE_WEIGHTS}
    active = [p for p in _SCORE_WEIGHTS
              if p == "engagement" or coverage[p] >= _SCORE_PILLAR_COVERAGE]
    den = sum(weights[p] for p in active) or 1
    # team medians of each driver metric — the concrete anchor next to each pillar
    # ("40h · team 15h"), so the relative score reads against real work.
    team_medians = {k: (statistics.median(v) if v else None) for k, v in dists.items()}

    band_spec = score_band_spec()        # once per run; see _score_band
    board = []
    for lg in eligible:
        e = raw[lg]
        pillars = per_pillars[lg]
        # missing an ACTIVE pillar → contributes 0 (a real minus); everyone with
        # enough activity is ranked, no gate — we don't drop people for gaps.
        num = sum(weights[p] * (pillars[p] or 0) for p in active)
        score = round(num / den)
        # integer point contributions per active pillar (value × weight-share) that
        # sum EXACTLY to the rounded score — largest-remainder rounding — so the row
        # arithmetic always adds up. None for pillars not scored this window.
        cf = {p: weights[p] * (pillars[p] or 0) / den for p in active}
        contrib = {p: int(cf[p]) for p in active}
        rem = score - sum(contrib.values())
        for p in sorted(active, key=lambda p: cf[p] - int(cf[p]), reverse=True)[:max(0, rem)]:
            contrib[p] += 1
        band, tone = _score_band(score, band_spec)
        # A band PER PILLAR, not just for the total. This deliberately runs the total's
        # thresholds over a pillar's percentile, which is a choice and not a derivation:
        # a pillar percentile and a weighted mean of pillar percentiles are different
        # quantities that happen to share a 0..100 range. It is here rather than in the
        # client so there is one place to change when the thresholds move — and they
        # probably will, because the median score is 50 by construction, so the share of
        # people below 45 is decided by the scale rather than by the work.
        pillar_bands = {}
        for p in _SCORE_WEIGHTS:
            if p in active and pillars[p] is not None:
                pb, pt = _score_band(pillars[p], band_spec)
                pillar_bands[p] = {"band": pb, "tone": pt}
            else:
                pillar_bands[p] = None
        board.append({
            "login": lg, "name": names.get(lg, lg), "score": score,
            "band": band, "tone": tone,
            "pillars": pillars, "pillar_bands": pillar_bands,
            "contributions": {p: contrib.get(p) for p in _SCORE_WEIGHTS},
            "drivers": {
                "commits": e["commits"], "loc": e["loc"], "prs_merged": e["prs_merged"],
                "prs_opened": e["prs_opened"], "ttm": e["ttm"], "size": e["size"],
                "rounds": e["rounds"], "merge_rate": e["merge_rate"],
                "reviews_given": e["reviews_given"], "specs": e["specs"],
                "flow": e["flow"], "ai_share": round(e["ai_share"]),
            },
        })
    board.sort(key=lambda x: (-(x["score"] or 0), x["name"].lower()))
    for i, row in enumerate(board):
        row["rank"] = i + 1
        # "why this rank": the person directly above and the pillar where they lead
        # most (by points), with that pillar's headline metric for both — so the gap
        # reads in real work, not abstract score.
        if i == 0:
            row["above"] = None
            continue
        ab = board[i - 1]
        gaps = {p: (ab["contributions"].get(p) or 0) - (row["contributions"].get(p) or 0)
                for p in active}
        gp = max(gaps, key=gaps.get) if gaps else None
        prim = _PILLAR_PRIMARY.get(gp) if gp else None
        row["above"] = {
            "name": ab["name"], "login": ab["login"], "score": ab["score"],
            "gap_total": ab["score"] - row["score"],
            "pillar": gp, "gap_pts": gaps.get(gp, 0) if gp else 0,
            "metric_label": prim["label"] if prim else None,
            "lower_better": prim["lower_better"] if prim else None,
            "mine": row["drivers"].get(prim["key"]) if prim else None,
            "theirs": ab["drivers"].get(prim["key"]) if prim else None,
        } if gp else None
    return {"weights": {k: round(v) for k, v in weights.items()},
            # Unrounded, for anything that has to REPRODUCE this run's arithmetic rather
            # than display it — score_delta's counterfactual would otherwise weight with
            # rounded numbers the scoring never used.
            "weights_raw": dict(weights),
            "active_pillars": active, "team_medians": team_medians,
            "min_activity": _SCORE_MIN_ACTIVITY,
            "n_eligible": len(eligible), "n_ranked": len(board), "board": board,
            # The per-signal sorted lists this run ranked against. Returned so a delta can
            # score one person's PREVIOUS drivers against THIS window's distribution — the
            # counterfactual that separates "the team moved" from "you moved". Rebuilding
            # them at the call site would be a second copy of the ranking rule.
            "dists": dists,
            "by_login": {r["login"]: r for r in board}}


def score_delta(cur: dict, prev: dict, login: str) -> dict | None:
    """How a person's score moved between two windows, split into the part that is theirs
    and the part that is the team moving around them. None when they are not in both.

    The score is a PERCENTILE, so it moves for two independent reasons and only one of them
    is anyone's to act on. Telling somebody they dropped eighteen points when eleven of those
    are the team improving is not a smaller version of the truth, it is a different claim —
    and it is the claim that gets made if a delta is reported as one number.

    The split is a counterfactual: score their PREVIOUS drivers against the CURRENT window's
    distribution. That is "you did not change, only the team did", so the difference from
    their previous score is the team's contribution and the rest is theirs. Measured on
    production this is not a rounding detail — one person fell 18 points, of which 11 was the
    team; another fell 26, of which 10 was.

    Uses cur["dists"], the very lists the current run ranked against, so the counterfactual
    and the real score cannot disagree about the ranking rule."""
    import bisect                    # local, as in developer_scores
    a, b = prev.get("by_login", {}).get(login), cur.get("by_login", {}).get(login)
    if not a or not b:
        return None
    dists, weights = cur["dists"], (cur.get("weights_raw") or cur["weights"])
    active = cur["active_pillars"]

    def pctl(key, v, direction):
        vals = dists.get(key) or []
        n = len(vals)
        if n <= 1:
            return 0.5
        below = bisect.bisect_left(vals, v)
        equal = bisect.bisect_right(vals, v) - below
        p = (below + 0.5 * equal) / n
        return p if direction > 0 else (1.0 - p)

    buckets: dict = {}
    for pillar, key, direction in _SCORE_SIGNALS:
        v = a["drivers"].get(key)
        if v is not None:
            buckets.setdefault(pillar, []).append(pctl(key, v, direction))
    pil = {p: (round(100 * sum(buckets[p]) / len(buckets[p])) if p in buckets else None)
           for p in _SCORE_WEIGHTS}
    den = sum(weights[p] for p in active) or 1
    counterfactual = round(sum(weights[p] * (pil[p] or 0) for p in active) / den)

    team = counterfactual - a["score"]
    return {"prev": a["score"], "now": b["score"], "total": b["score"] - a["score"],
            "team": team, "you": b["score"] - counterfactual,
            "pillars": {p: {"prev": a["pillars"].get(p), "now": b["pillars"].get(p),
                            "prev_points": a["contributions"].get(p),
                            "now_points": b["contributions"].get(p)}
                        for p in _SCORE_WEIGHTS if p in active}}


def compare_row_to(row, anchor, active):
    """How `row` stacks up against `anchor` — the person whose page is open — for the
    team leaderboard's per-row comparison ("ahead of / behind you"). Returns the score
    delta (+ = row is ahead of the anchor) and the single pillar that most explains the
    gap, with that pillar's headline metric for both. {'self': True} for the anchor's
    own row; None when there's nothing to compare."""
    if not anchor or row.get("login") == anchor.get("login"):
        return {"self": True}
    diffs = {p: (row["contributions"].get(p) or 0) - (anchor["contributions"].get(p) or 0)
             for p in (active or [])}
    if not diffs:
        return None
    gp = max(diffs, key=lambda p: abs(diffs[p]))
    prim = _PILLAR_PRIMARY.get(gp)
    return {
        "delta": (row["score"] or 0) - (anchor["score"] or 0),
        "pillar": gp,
        "metric_label": prim["label"] if prim else None,
        "lower_better": prim["lower_better"] if prim else None,
        "row_val": row["drivers"].get(prim["key"]) if prim else None,
        "anchor_val": anchor["drivers"].get(prim["key"]) if prim else None,
    }


def score_summary(conn, since, until, repos=None, top_n=8):
    """Org-level rollup of the developer score for the Overview: band distribution,
    top-N leaders, per-company medians, and the team's real per-pillar medians. None
    when nobody is scored. The score is org-relative, so a plain median hovers near the
    middle by construction — the band split and per-company view show shape/skew, and
    the pillar medians are the concrete team numbers behind it."""
    import collections
    sc = developer_scores(conn, since, until, repos)
    board = sc["board"]
    if not board:
        return None
    comp = {r["login"]: (r["company"] or "Other")
            for r in conn.execute("SELECT login, company FROM person")}
    bandc = collections.Counter(r["band"] for r in board)
    bands = [{"band": b, "tone": t, "n": bandc.get(b, 0)}
             for b, t in (("Strong", "good"), ("Solid", "good"),
                          ("Developing", "warn"), ("Building", "weak"))]
    by = collections.defaultdict(list)
    for r in board:
        by[comp.get(r["login"], "Other")].append(r["score"])
    by_company = sorted(
        ({"company": c, "n": len(v), "median": round(statistics.median(v)),
          "mean": round(sum(v) / len(v))} for c, v in by.items()),
        key=lambda x: (-x["median"], -x["n"]))
    scores = [r["score"] for r in board]
    return {"n": len(board), "median": round(statistics.median(scores)),
            "top": board[:top_n], "bands": bands, "by_company": by_company,
            "team_medians": sc["team_medians"], "active_pillars": sc["active_pillars"],
            "weights": sc["weights"]}


def person_login_for(conn, ident: str):
    """Resolve an OAuth identity to a canonical person login. Sign-in is GitHub-based,
    so the proxy's username IS the login — match it case-insensitively; fall back to an
    email match for email-only headers. Returns None if no person matches."""
    ident = (ident or "").strip()
    if not ident:
        return None
    row = conn.execute(
        "SELECT login FROM person WHERE lower(login)=lower(?) LIMIT 1", (ident,)).fetchone()
    if row:
        return row["login"]
    if "@" in ident:
        low = ident.lower()
        for r in conn.execute("SELECT login, emails FROM person WHERE emails<>''"):
            if low in (r["emails"] or "").lower():
                return r["login"]
    return None


# --- usage analytics of the report itself (meta-analytics) -----------------
# Who (as a persona) opens the report and which widgets they view. Identity is
# resolved by the CALLER server-side from the oauth2-proxy headers — never from
# the client payload. Callers wrap these so a locked DB never breaks a request.
_USAGE_KINDS = {"page", "tab", "panel", "drill", "chat_open", "chat_msg"}


def _usage_clip(v, n: int = 120):
    s = ("" if v is None else str(v)).strip()
    return s[:n] or None


def _usage_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _usage_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def record_usage_events(conn, viewer_login, viewer_ident, events) -> int:
    """Append usage events (ts server-stamped ISO8601 UTC). `events` is a list of
    dicts with keys kind, target, tab, dwell_ms, period, session_id, and — for
    chat_msg — tokens_in, tokens_out, cost_usd. Unknown kinds are dropped. Returns
    the number of rows written."""
    ts = _utc_iso()
    ident = (viewer_ident or "anon")[:120]
    rows = []
    for e in events:
        kind = (e.get("kind") or "").strip()
        if kind not in _USAGE_KINDS:
            continue
        rows.append((
            ts, _usage_clip(e.get("session_id")), viewer_login, ident, kind,
            _usage_clip(e.get("target")), _usage_clip(e.get("tab")),
            _usage_int(e.get("dwell_ms")), _usage_clip(e.get("period")),
            _usage_int(e.get("tokens_in")), _usage_int(e.get("tokens_out")),
            _usage_int(e.get("tokens_cached")), _usage_float(e.get("cost_usd")),
        ))
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO usage_event "
        "(ts, session_id, viewer_login, viewer_ident, kind, target, tab, dwell_ms, period, "
        " tokens_in, tokens_out, tokens_cached, cost_usd) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)


def create_dashboard(conn, owner_login, title, spec, visibility="private") -> str:
    did = "dash_" + os.urandom(6).hex()
    ts = _utc_iso()
    conn.execute(
        "INSERT INTO dashboard (id, owner_login, title, visibility, spec, "
        " created_ts, updated_ts) VALUES (?,?,?,?,?,?,?)",
        (did, owner_login, title or "Untitled", visibility,
         json.dumps(spec, ensure_ascii=False), ts, ts))
    conn.commit()
    return did


def get_dashboard(conn, did):
    r = conn.execute("SELECT * FROM dashboard WHERE id=?", (did,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["spec"] = json.loads(d["spec"])
    return d


def list_dashboards(conn, owner_login) -> list:
    """The viewer's own dashboards + all shared ones, newest first."""
    rows = conn.execute(
        "SELECT id, owner_login, title, visibility, updated_ts FROM dashboard "
        "WHERE owner_login=? OR visibility='shared' ORDER BY updated_ts DESC",
        (owner_login,))
    return [dict(r) for r in rows]


def update_dashboard(conn, did, title=None, spec=None, visibility=None) -> None:
    sets, params = ["updated_ts=?"], [_utc_iso()]
    if title is not None:
        sets.append("title=?"); params.append(title)
    if spec is not None:
        sets.append("spec=?"); params.append(json.dumps(spec, ensure_ascii=False))
    if visibility is not None:
        sets.append("visibility=?"); params.append(visibility)
    params.append(did)
    conn.execute(f"UPDATE dashboard SET {', '.join(sets)} WHERE id=?", params)
    conn.commit()


def delete_dashboard(conn, did) -> None:
    conn.execute("DELETE FROM dashboard WHERE id=?", (did,))
    conn.commit()


def record_chat_message(conn, session_id, viewer_login, viewer_ident, role, text,
                        view=None, period=None, tokens_in=None, tokens_out=None,
                        tokens_cached=None, cost_usd=None) -> int:
    """Append one transcript row (ts server-stamped). `text` is the CLEAN content —
    callers must strip any server-added context/identity annotations first. Returns
    the new row id."""
    cur = conn.execute(
        "INSERT INTO chat_message (ts, session_id, viewer_login, viewer_ident, role, "
        " text, view, period, tokens_in, tokens_out, tokens_cached, cost_usd) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (_utc_iso(), _usage_clip(session_id), viewer_login, (viewer_ident or "anon")[:120],
         role, text or "", _usage_clip(view), _usage_clip(period),
         _usage_int(tokens_in), _usage_int(tokens_out), _usage_int(tokens_cached),
         _usage_float(cost_usd)))
    conn.commit()
    return cur.lastrowid


def record_chat_tool_calls(conn, session_id, viewer_login, viewer_ident,
                           message_id, calls) -> int:
    """Persist the tool calls of one turn. Each `calls` item is
    {name, args: dict, result, ok: bool}. args/result are JSON-serialised and
    truncated (full result size kept in result_bytes). Returns rows written."""
    if not calls:
        return 0
    ts = _utc_iso()
    ident = (viewer_ident or "anon")[:120]
    sid = _usage_clip(session_id)
    rows = []
    for i, c in enumerate(calls):
        args = json.dumps(c.get("args") or {}, ensure_ascii=False, default=str)
        res = json.dumps(c.get("result"), ensure_ascii=False, default=str)
        rows.append((ts, sid, viewer_login, ident, message_id, i,
                     (c.get("name") or "")[:80], args[:2000], res[:4000], len(res),
                     1 if c.get("ok") else 0))
    conn.executemany(
        "INSERT INTO chat_tool_call (ts, session_id, viewer_login, viewer_ident, "
        " message_id, seq, tool_name, args, result, result_bytes, ok) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)


def prune_chat_messages(conn, days: int) -> int:
    """Delete transcript rows and their tool calls older than `days` (retention).
    No-op when days<=0 (keep forever). Returns chat_message rows removed."""
    if not days or days <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute("DELETE FROM chat_message WHERE ts < ?", (cutoff,))
    conn.execute("DELETE FROM chat_tool_call WHERE ts < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def chat_conversations(conn, since: str, until: str, session_id: str = "",
                       login: str = "", limit: int = 500) -> list:
    """Transcript rows in [since, until], newest first — optionally filtered to one
    session_id or viewer login. For the Usage insights conversation view."""
    lo, hi = since[:10] + "T00:00:00Z", until[:10] + "T23:59:59Z"
    clauses, params = ["ts >= ?", "ts <= ?"], [lo, hi]
    if session_id:
        clauses.append("session_id = ?"); params.append(session_id)
    if login:
        clauses.append("viewer_login = ?"); params.append(login)
    where = " AND ".join(clauses)
    return [dict(r) for r in conn.execute(
        f"SELECT id, ts, session_id, COALESCE(viewer_login, viewer_ident, '(anon)') AS who, "
        f"role, text, view, period, tokens_in, tokens_out, tokens_cached, cost_usd "
        f"FROM chat_message WHERE {where} ORDER BY id DESC LIMIT {int(limit)}", params)]


def chat_sessions(conn, since: str, until: str, limit: int = 200) -> list:
    """Conversation list for the (unlinked) chat-log viewer: one row per session in
    [since, until], newest activity first."""
    lo, hi = since[:10] + "T00:00:00Z", until[:10] + "T23:59:59Z"
    return [dict(r) for r in conn.execute(
        "SELECT COALESCE(session_id, '') AS session_id, "
        "COALESCE(viewer_login, viewer_ident, '(anon)') AS who, "
        "MIN(ts) AS started, MAX(ts) AS last, "
        "SUM(CASE WHEN role='user' THEN 1 ELSE 0 END) AS questions, "
        "COALESCE(SUM(tokens_in),0)+COALESCE(SUM(tokens_out),0) AS tokens, "
        "SUM(cost_usd) AS cost "
        "FROM chat_message WHERE ts >= ? AND ts <= ? "
        "GROUP BY session_id, who ORDER BY last DESC LIMIT ?", (lo, hi, int(limit)))]


def chat_session_detail(conn, session_id: str) -> dict:
    """Full transcript for one session (chronological) plus the tool calls of each
    assistant turn, keyed by message id."""
    if session_id:
        where, params = "session_id = ?", (session_id,)
    else:
        where, params = "session_id IS NULL OR session_id = ''", ()
    msgs = [dict(r) for r in conn.execute(
        f"SELECT id, ts, COALESCE(viewer_login, viewer_ident, '(anon)') AS who, role, text, "
        f"view, period, tokens_in, tokens_out, tokens_cached, cost_usd "
        f"FROM chat_message WHERE {where} ORDER BY id ASC", params)]
    tools = {}
    for r in conn.execute(
            f"SELECT message_id, seq, tool_name, args, ok, result_bytes FROM chat_tool_call "
            f"WHERE {where} ORDER BY id ASC", params):
        d = dict(r)
        tools.setdefault(d["message_id"], []).append(d)
    return {"session_id": session_id, "messages": msgs, "tools": tools}


def record_page_open(conn, viewer_login, viewer_ident) -> int:
    """Log one server-side page-open (session_id NULL — the JS-independent,
    authoritative adoption signal). See record_usage_events."""
    return record_usage_events(conn, viewer_login, viewer_ident, [{"kind": "page"}])


def usage_summary(conn, since: str, until: str) -> dict:
    """Aggregate report-usage events in [since, until], bounds padded to full days
    like serve_custom_period. `by_widget` EXCLUDES the 'all' tab (it renders every
    panel at once, so one scroll would mark the whole report seen); `by_persona`
    lists resolved personas only. Counts of DISTINCT viewer_login ignore NULLs."""
    lo = since[:10] + "T00:00:00Z"
    hi = until[:10] + "T23:59:59Z"
    win = "ts >= ? AND ts <= ?"
    a = (lo, hi)

    opens = conn.execute(
        f"SELECT COUNT(*) FROM usage_event WHERE kind='page' AND {win}", a).fetchone()[0]
    unique_personas = conn.execute(
        f"SELECT COUNT(DISTINCT viewer_login) FROM usage_event WHERE {win}", a).fetchone()[0]

    by_widget = [dict(r) for r in conn.execute(
        f"SELECT target, COUNT(*) AS views, COUNT(DISTINCT viewer_login) AS unique_viewers "
        f"FROM usage_event WHERE kind='panel' AND target IS NOT NULL "
        f"AND (tab IS NULL OR tab <> 'all') AND {win} "
        f"GROUP BY target ORDER BY views DESC", a)]
    by_tab = [dict(r) for r in conn.execute(
        f"SELECT target, COUNT(*) AS views, COUNT(DISTINCT viewer_login) AS unique_viewers "
        f"FROM usage_event WHERE kind='tab' AND target IS NOT NULL AND {win} "
        f"GROUP BY target ORDER BY views DESC", a)]
    by_drill = [dict(r) for r in conn.execute(
        f"SELECT target, COUNT(*) AS views, COUNT(DISTINCT viewer_login) AS unique_viewers "
        f"FROM usage_event WHERE kind='drill' AND target IS NOT NULL AND {win} "
        f"GROUP BY target ORDER BY views DESC", a)]
    by_persona = [dict(r) for r in conn.execute(
        f"SELECT viewer_login AS login, "
        f"SUM(CASE WHEN kind='page' THEN 1 ELSE 0 END) AS opens, "
        f"COUNT(DISTINCT CASE WHEN kind='panel' THEN target END) AS widgets_seen, "
        f"SUM(CASE WHEN kind='chat_msg' THEN 1 ELSE 0 END) AS chat_msgs "
        f"FROM usage_event WHERE viewer_login IS NOT NULL AND {win} "
        f"GROUP BY viewer_login ORDER BY opens DESC, widgets_seen DESC", a)]

    # Metrics-assistant usage: panel opens, questions asked, distinct askers, and the
    # report view each question was asked from (chat_msg.target).
    chat_opens = conn.execute(
        f"SELECT COUNT(*) FROM usage_event WHERE kind='chat_open' AND {win}", a).fetchone()[0]
    crow = conn.execute(
        f"SELECT COUNT(*) AS msgs, "
        f"COALESCE(SUM(tokens_in),0) AS tin, COALESCE(SUM(tokens_out),0) AS tout, "
        f"COALESCE(SUM(tokens_cached),0) AS tcached, SUM(cost_usd) AS cost "
        f"FROM usage_event WHERE kind='chat_msg' AND {win}", a).fetchone()
    chat_msgs, chat_tokens_in = crow["msgs"], crow["tin"]
    chat_tokens_out, chat_cost = crow["tout"], crow["cost"]
    chat_tokens_cached = crow["tcached"]
    # cache efficiency: share of input tokens served from the context cache.
    chat_cache_hit_pct = round(100 * chat_tokens_cached / chat_tokens_in, 1) \
        if chat_tokens_in else 0.0
    chat_users = conn.execute(
        f"SELECT COUNT(DISTINCT viewer_login) FROM usage_event "
        f"WHERE kind IN ('chat_open','chat_msg') AND viewer_login IS NOT NULL AND {win}",
        a).fetchone()[0]
    by_chat_view = [dict(r) for r in conn.execute(
        f"SELECT target, COUNT(*) AS views, COUNT(DISTINCT viewer_login) AS unique_viewers, "
        f"COALESCE(SUM(tokens_in),0)+COALESCE(SUM(tokens_out),0) AS tokens, SUM(cost_usd) AS cost "
        f"FROM usage_event WHERE kind='chat_msg' AND target IS NOT NULL AND {win} "
        f"GROUP BY target ORDER BY views DESC", a)]
    by_chat_tool = [dict(r) for r in conn.execute(
        f"SELECT tool_name, COUNT(*) AS calls, COUNT(DISTINCT viewer_login) AS unique_callers, "
        f"SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) AS errors "
        f"FROM chat_tool_call WHERE {win} GROUP BY tool_name ORDER BY calls DESC", a)]

    return {
        "since": lo[:10], "until": hi[:10],
        "opens": opens, "unique_personas": unique_personas,
        "by_widget": by_widget, "by_tab": by_tab, "by_drill": by_drill,
        "by_persona": by_persona,
        "chat_opens": chat_opens, "chat_msgs": chat_msgs, "chat_users": chat_users,
        "chat_tokens_in": chat_tokens_in, "chat_tokens_out": chat_tokens_out,
        "chat_tokens": chat_tokens_in + chat_tokens_out, "chat_cost_usd": chat_cost,
        "chat_tokens_cached": chat_tokens_cached, "chat_cache_hit_pct": chat_cache_hit_pct,
        "by_chat_view": by_chat_view, "by_chat_tool": by_chat_tool,
    }


_USAGE_DETAIL_KIND = {"widget": "panel", "tab": "tab", "drill": "drill"}


def _chat_requests(conn, lo, hi, clause="", params=(), limit=300):
    """Individual chat_msg events (newest first) — one row per question. NO question
    text is stored; a request is (when, who, view, period, tokens, cost)."""
    rows = conn.execute(
        f"SELECT ts, COALESCE(viewer_login, viewer_ident, '(anon)') AS who, "
        f"target AS view, period, "
        f"COALESCE(tokens_in,0)+COALESCE(tokens_out,0) AS tokens, "
        f"COALESCE(tokens_cached,0) AS cached, cost_usd AS cost "
        f"FROM usage_event WHERE kind='chat_msg' {clause} AND ts >= ? AND ts <= ? "
        f"ORDER BY ts DESC LIMIT {int(limit)}", (*params, lo, hi)).fetchall()
    return [dict(r) for r in rows]


def usage_detail(conn, since: str, until: str, by: str, key: str) -> dict:
    """Drill behind a Usage insights row, over the same padded [since, until].
      by='widget'|'tab'|'drill' → who viewed `key`: [{who, views}] (resolved
        personas plus a single '(unresolved)' bucket). Widgets exclude the 'all' tab.
      by='persona'              → what login=`key` engaged with, incl. its chat_log.
      by='chatlog'              → every assistant request (key ignored).
      by='chat'                 → assistant requests asked from view `key`."""
    lo = since[:10] + "T00:00:00Z"
    hi = until[:10] + "T23:59:59Z"
    win = "ts >= ? AND ts <= ?"

    if by == "persona":
        def _for(clause):
            return [dict(r) for r in conn.execute(
                f"SELECT target, COUNT(*) AS views FROM usage_event "
                f"WHERE viewer_login = ? AND {clause} AND target IS NOT NULL AND {win} "
                f"GROUP BY target ORDER BY views DESC", (key, lo, hi))]
        return {"by": by, "key": key,
                "widgets": _for("kind='panel' AND (tab IS NULL OR tab <> 'all')"),
                "tabs": _for("kind='tab'"), "drills": _for("kind='drill'"),
                "chat_log": _chat_requests(conn, lo, hi, "AND viewer_login = ?", (key,))}

    if by == "chatlog":
        return {"by": by, "key": key, "requests": _chat_requests(conn, lo, hi)}
    if by == "chat":
        return {"by": by, "key": key,
                "requests": _chat_requests(conn, lo, hi, "AND target = ?", (key,))}
    if by == "tool":
        # recent calls of tool `key`, args first — for sql_query the args ARE the SQL,
        # so this is the "which raw queries recur → promote to a tool" view.
        calls = [dict(r) for r in conn.execute(
            f"SELECT ts, COALESCE(viewer_login, viewer_ident, '(anon)') AS who, "
            f"args, ok, result_bytes FROM chat_tool_call "
            f"WHERE tool_name = ? AND {win} ORDER BY id DESC LIMIT 300", (key, lo, hi))]
        return {"by": by, "key": key, "calls": calls}

    kind = _USAGE_DETAIL_KIND.get(by)
    if not kind:
        return {"by": by, "key": key, "viewers": []}
    extra = " AND (tab IS NULL OR tab <> 'all')" if kind == "panel" else ""
    viewers = [dict(r) for r in conn.execute(
        f"SELECT COALESCE(viewer_login, '(unresolved)') AS who, COUNT(*) AS views "
        f"FROM usage_event WHERE kind='{kind}' AND target = ?{extra} AND {win} "
        f"GROUP BY who ORDER BY views DESC", (key, lo, hi))]
    return {"by": by, "key": key, "viewers": viewers}


def export_jsonl(conn: sqlite3.Connection) -> None:
    """Mirror the durable tables to history/*.jsonl so a text, diffable backup
    can be committed (the .db itself is git-ignored). DB stays the source of
    truth; this is a derived artifact, regenerated every run — no drift."""
    hist = str(paths.data_path("history"))
    os.makedirs(hist, exist_ok=True)
    with open(os.path.join(hist, "traffic.jsonl"), "w") as fh:
        for r in read_traffic(conn):
            fh.write(json.dumps(r) + "\n")
    with open(os.path.join(hist, "snapshots.jsonl"), "w") as fh:
        for r in read_snapshots(conn):
            fh.write(json.dumps(r) + "\n")
    # forward-only Projects v2 status history — cannot be re-collected, so the
    # diffable backup matters more here than anywhere else
    with open(os.path.join(hist, "work_item_status.jsonl"), "w") as fh:
        for r in read_work_item_status(conn):
            fh.write(json.dumps(r) + "\n")


# --- one-time migration from the legacy JSONL files ------------------------
def seed_from_jsonl(conn: sqlite3.Connection) -> dict:
    """Import existing history/*.jsonl into the DB if their tables are empty.
    Idempotent: runs only while a table has no rows, so it never double-imports."""
    out = {"traffic": 0, "snapshots": 0}
    hist = str(paths.data_path("history"))
    tpath = os.path.join(hist, "traffic.jsonl")
    if conn.execute("SELECT COUNT(*) FROM traffic").fetchone()[0] == 0 and os.path.exists(tpath):
        rows = []
        with open(tpath) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if rows:
            out["traffic"] = upsert_traffic(conn, rows)
    spath = os.path.join(hist, "snapshots.jsonl")
    if conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0 and os.path.exists(spath):
        n = 0
        with open(spath) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    upsert_snapshot(conn, json.loads(line)); n += 1
                except json.JSONDecodeError:
                    continue
        out["snapshots"] = n
    dpath = str(paths.data_path("data.json"))
    if conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0 and os.path.exists(dpath):
        try:
            with open(dpath) as fh:
                upsert_run(conn, json.load(fh))
            out["runs"] = 1
        except (json.JSONDecodeError, OSError, KeyError):
            pass
    return out


# --- metric registry: metrics computed in this module, tied to their functions ---
_mreg.register_for(aggregate, [
    _m("commits", type="direct", group="volume", unit="count",
       desc="Non-merge commits on each repo's default branch, attributed to the commit's "
            "GitHub author. Bots and migration-copy commits are excluded.",
       formula="COUNT(commits) where is_bot=0, author<>'', committed_at in [since, until]",
       snippet="SELECT COUNT(*) FROM commits\n"
               "WHERE is_bot=0 AND author_login<>'' AND committed_at BETWEEN ? AND ?"),
    _m("meaningful_additions", type="computed", group="volume", unit="lines",
       desc="Added lines excluding generated / vendored / dependency / fixture / lockfile / "
            "binary paths, so the number reflects hand-relevant code.",
       formula="SUM(meaningful_additions) — additions on files NOT matching meaningful_loc excludes",
       snippet="SELECT SUM(meaningful_additions) FROM commits WHERE is_bot=0 AND …"),
    _m("people", type="computed", group="volume", unit="count",
       desc="Distinct contributors with any counted activity in the window — a commit, PR, "
            "issue, or spec edit. Aliased logins fold into their primary identity.",
       formula="COUNT(DISTINCT login) where commits+prs+issues+specs > 0",
       snippet="tot['people'] = sum(1 for p in people.values()\n"
               "    if p['commits'] + p['prs'] + p['issues'] + p['specs'] > 0)"),
    _m("commit_mix", type="computed", group="code", unit="%",
       desc="Share of commits that touch a spec doc vs pure code.",
       formula="pct_specs = SUM(is_spec) / COUNT(*) ; pct_code = 1 − pct_specs",
       snippet="SELECT SUM(is_spec) specs, COUNT(*) total FROM commits WHERE …\n"
               "pct_specs = round(100 * specs / total, 1)"),
    _m("split", type="computed", group="code", unit="%",
       desc="Where effort goes, split across the configurable repository types (e.g. platform "
            "vs app, plus any custom types). Classification is per repo, editable in Config.",
       formula="GROUP commits/PRs/LOC BY commits.classification (one series per repo type)",
       snippet="SELECT classification, COUNT(*) FROM commits WHERE … GROUP BY classification"),
    _m("element_rows", type="computed", group="code", unit="rollup",
       desc="Per-product-element rollup (Insight, Studio, Gears, …). Each repo maps to one "
            "element; commits/LOC/PRs sum per element.",
       formula="GROUP metrics BY repo.element (exact name > glob > default)",
       snippet="SELECT rr.element, COUNT(*) FROM commits c\n"
               "JOIN repo rr ON rr.key = c.repo GROUP BY rr.element"),
    _m("commit_types", type="computed", group="code", unit="count",
       desc="Conventional-commit type rollup (feat / fix / docs / …); non-conventional "
            "subjects bucket as 'other'.",
       formula="GROUP commits BY commit_type",
       snippet="SELECT commit_type, COUNT(*) FROM commits WHERE … GROUP BY commit_type"),
    _m("specs", type="computed", group="delivery", unit="count",
       desc="Commits touching a spec doc, deduped by commit sha.",
       formula="COUNT(commits) where is_spec=1, in window",
       snippet="SELECT SUM(is_spec) FROM commits WHERE …"),
    _m("prs", type="direct", group="delivery", unit="count",
       desc="Pull requests opened by the author in the window. Migration-recreated PRs "
            "and bots are excluded.",
       formula="COUNT(pull_request) where is_bot=0, is_migration=0, created_at in window",
       snippet="SELECT COUNT(*) FROM pull_request\n"
               "WHERE is_bot=0 AND is_migration=0 AND created_at BETWEEN ? AND ?"),
    _m("prs_merged", type="direct", group="delivery", unit="count",
       desc="Of the opened PRs, those with a merge timestamp.",
       formula="COUNT(pull_request) where merged_at IS NOT NULL",
       snippet="SUM(CASE WHEN merged_at IS NOT NULL AND merged_at<>'' THEN 1 ELSE 0 END)"),
    _m("bugs", type="direct", group="delivery", unit="count",
       desc="Issues resolving to the 'bug' category via the semantic taxonomy.",
       formula="COUNT(issue) where is_bug=1, in window",
       snippet="SELECT SUM(is_bug) FROM issue WHERE is_bot=0 AND created_at BETWEEN ? AND ?"),
    _m("epics", type="direct", group="delivery", unit="count",
       desc="Issues resolving to the 'epic' category via the semantic taxonomy.",
       formula="COUNT(issue) where is_epic=1, in window",
       snippet="SELECT SUM(is_epic) FROM issue WHERE is_bot=0 AND created_at BETWEEN ? AND ?"),
    _m("features", type="direct", group="delivery", unit="count",
       desc="Issues resolving to the 'feature' category via the semantic taxonomy.",
       formula="COUNT(issue) where is_feature=1, in window",
       snippet="SELECT SUM(is_feature) FROM issue WHERE …"),
    _m("median_ttm_h", type="computed", group="delivery", unit="hours",
       desc="Median hours from PR open to merge, over the window's merged PRs.",
       formula="median(merged_at − created_at) in hours",
       snippet="ttms.append((merged - created).total_seconds() / 3600)\n"
               "median_ttm_h = statistics.median(ttms)"),
    _m("reviews", type="direct", group="review", unit="count",
       desc="PR reviews submitted as a reviewer (any state) in the window.",
       formula="COUNT(review) by reviewer_login",
       snippet="SELECT reviewer_login, COUNT(*) FROM review WHERE submitted_at BETWEEN ? AND ?"),
    _m("approvals", type="direct", group="review", unit="count",
       desc="Reviews whose state is APPROVED.",
       formula="COUNT(review) where state='APPROVED'",
       snippet="a['approvals'] += 1 if state == 'APPROVED' else 0"),
    _m("coverage_pct", type="computed", group="review", unit="%",
       desc="Share of PRs in the window that received at least one review.",
       formula="reviewed_prs / total_prs × 100",
       snippet="coverage_pct = round(100 * len(reviewed_pairs) / total_prs, 1)"),
    _m("ai_pct", type="computed", group="ai", unit="%",
       desc="Share of a company's / element's commits that carry an AI-tool marker.",
       formula="ai_commits / commits × 100",
       snippet="ai_pct = round(100 * ai_commits / commits, 1) if commits else 0"),
    _m("company_rows", type="computed", group="company", unit="rollup",
       desc="Metrics grouped by each person's company. Company = per-person override > "
            "email-domain rule > 'Other'.",
       formula="GROUP metrics BY person.company",
       snippet="co = comp_of.get(login, 'Other')\ncompany[co]['commits'] += p['commits']"),
    _m("categories", type="computed", group="company", unit="%",
       desc="Per-person share within each category (code, LOC, specs, bugs, features), ranked.",
       formula="person.value / category.total × 100, sorted desc",
       snippet="pct = round(100 * value / total, 1)"),
    _m("concentration", type="computed", group="company", unit="%",
       desc="How concentrated a category is: sum of the top-3 shares, and how many people it "
            "takes to reach 80% cumulative — a bus-factor signal.",
       formula="top3 = Σ top-3 pct ; n80 = min N with Σ pct ≥ 80",
       snippet="cum = 0\nfor n, r in enumerate(rows, 1):\n"
               "    cum += r['pct']\n    if cum >= 80: n80 = n; break"),
    _m("spark", type="computed", group="trend", unit="series",
       desc="Commits (and LOC) bucketed into 12 equal time slices across the selected window, "
            "drawn as a tiny line under the KPI.",
       formula="bucket = floor((day − since) / span × 12) ; count per bucket",
       snippet="b = int((day - s0).days / (span+1) * 12)\ncb[b] += count"),
    _m("clones", type="direct", group="usage", unit="count",
       desc="Git clones and distinct cloners from GitHub's traffic API, accumulated daily so "
            "any window sums (partial before collection began; push-access repos only).",
       formula="SUM(clones / clone_uniques) over the window from the daily traffic table",
       snippet="SELECT SUM(clones), SUM(clone_uniques) FROM traffic WHERE date BETWEEN ? AND ?"),
    _m("views", type="direct", group="usage", unit="count",
       desc="Page views and unique visitors from the traffic API, same accumulation model.",
       formula="SUM(views / view_uniques) over the window",
       snippet="SELECT SUM(views), SUM(view_uniques) FROM traffic WHERE …"),
])

_mreg.register_for(person_profile, [
    _m("rank_share", type="computed", group="company", unit="rank / %",
       desc="On the Person tab: the person's rank by commits among all contributors in the "
            "window, and their share of the org total per metric.",
       formula="rank = position in ORDER BY commits DESC ; share = person/org × 100",
       snippet="ranks = [login for login,_ in ORDER BY COUNT(*) DESC]\n"
               "rank = ranks.index(login) + 1"),
    _m("merge_rate", type="computed", group="delivery", unit="%",
       desc="Share of a person's opened PRs that merged (all-time, on the Person tab).",
       formula="merged_prs / prs_opened × 100",
       snippet="(100 * merged_prs / prs) if prs else '—'"),
])

_mreg.register_for(contributors_timeseries, [
    _m("contributors", type="computed", group="trend", unit="count",
       desc="Distinct contributor count as of each date — a login with any commit/PR/issue "
            "by that date (cumulative, all-time).",
       formula="COUNT(DISTINCT login with activity ≤ date), per date",
       snippet="SELECT COUNT(DISTINCT author_login) … WHERE committed_at <= :date"),
])


_mreg.register_for(weekly_activity, [
    _m("weekly_activity", type="computed", group="trend", unit="rollup",
       desc="Per-person commits & meaningful-LOC bucketed into weeks for the activity heat-map "
            "over the window.",
       formula="GROUP commits + meaningful_additions BY (author, week) across the window",
       snippet="SELECT author_login, week, COUNT(*), SUM(meaningful_additions) FROM commits "
               "WHERE … GROUP BY author_login, week"),
])

_mreg.register_for(developer_scores, [
    _m("developer_score", type="computed", group="score", unit="0–100",
       desc="EXPERIMENTAL compound per-person score: each input signal is turned into a "
            "percentile within the people active in the window, averaged inside four pillars, "
            "then combined as a weighted mean. Transparent heuristic (no ML); directional, not "
            "a verdict. Everyone with ≥5 commits+PRs is ranked; a SCORED pillar you have no "
            "data for (e.g. no PRs opened) counts as 0 — a real minus, not a free pass — rather "
            "than dropping you from the board. A pillar with data across fewer than half the team "
            "is a collection gap, not a shortfall, so it's left out for everyone. Weights are "
            "tunable and calibrated by the score backtest.",
       formula="active = pillars with team coverage ≥ 50% (engagement always in); "
               "score = Σ_{p∈active} wₚ·(pillarₚ or 0) / Σ_{p∈active} wₚ ×100 "
               "(missing active pillar → 0); defaults engagement 20, delivery 25, craft 25, flow 35; "
               "pillarₚ = mean(percentileₛ) over its signals",
       snippet="pct(x) = (#below + 0.5·#equal) / N   # within eligible people, per signal\n"
               "pillar = 100·mean(pct of that pillar's signals)\n"
               "score  = round(Σ weight·pillar / Σ weight)"),
    _m("score_engagement", type="computed", group="score", unit="0–100",
       desc="Engagement pillar (weight 20): output + participation — commits, meaningful LOC, PRs "
            "merged, reviews given and spec edits, each as an org percentile. Throughput and "
            "Collaboration were merged here after the backtest found them ~0.86 correlated "
            "(double-counting volume). Deliberately the smallest weight.",
       formula="mean(pctl of commits, meaningful_additions, prs_merged, reviews_given, specs) ×100",
       snippet="signals: commits(+), meaningful_additions(+), prs_merged(+), reviews_given(+), specs(+)"),
    _m("score_delivery", type="computed", group="score", unit="0–100",
       desc="Delivery pillar (weight 25): how quickly and in what size work ships — median "
            "time-to-merge and median PR size (changed files). Lower is better, so the percentile "
            "is inverted.",
       formula="mean(pctl(−median_ttm), pctl(−median_changed_files)) ×100",
       snippet="ttm_h = (merged_at − created_at); size = changed_files  # both lower-is-better"),
    _m("score_craft", type="computed", group="score", unit="0–100",
       desc="Craft & rework pillar (weight 25): how clean the work is — average review rounds per "
            "merged PR (lower better) and merge rate (merged/opened, higher better). Proxy for "
            "quality; does NOT use revert/reopen blame (ambiguous per author).",
       formula="mean(pctl(−avg_review_rounds), pctl(merge_rate)) ×100",
       snippet="rounds = AVG(review_count) over merged PRs; merge_rate = prs_merged / prs_opened"),
    _m("score_flow", type="computed", group="score", unit="0–100",
       desc="Flow pillar (weight 35): how smoothly a person's items move, from RETROSPECTIVE "
            "issue/PR timeline events (not the history-less Projects-v2 board). Friction per "
            "owned item = 2·bounces (convert_to_draft / reopened) + extra review-requests + "
            "extra assignments; lower is smoother. Independent of the commit/PR volume signals. "
            "People with <3 tracked items are reweighted out.",
       formula="friction/item = (2·bounces + churn) / owned items; percentile inverted (lower→higher)",
       snippet="owner = PR author / issue first-assignee; events from timeline_event\n"
               "see semantic_metrics.person_flow()"),
])
