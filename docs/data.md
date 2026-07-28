# Data & storage

Where the data comes from, what is stored, and what cannot be recovered once lost.

[← back to the README](../README.md)

## Data sources

Collection uses two source paths deliberately:

- **Git history clones** for commits, lines changed, spec-document edits, commit
  types, AI-tool markers, and content markers. This counts corporate commit
  emails that GitHub does not link to a login. Repos are cloned once into
  `.repos/`; each run refreshes the stable local ref `refs/report/head`. Clones
  are **full history** (no `--shallow-since`) and **with blobs** (not
  `--filter=blob:none`): full history is needed for accurate `git blame`
  attribution of `@cpt` code markers, and blobs are needed for `git log
  --numstat` (LOC) and `git grep` over file content. An older shallow clone is
  auto-deepened (`--unshallow`) on the next run. Git transfers only new objects
  after the first clone; metrics are recomputed from the lookback window each run.
- **GitHub API** for org membership, repo inventory, verified commit authors,
  PRs, issues, forks, stars, and clone/view traffic.

### Clone cache behavior

`.repos/` is a persistent implementation cache, not report output. It is safe
to delete a repo directory there when you want to force a clean clone; the next
refresh will recreate it.

During refresh the log has two git phases per primary-org repo:

- `Clone+log: <repo>` — refreshes the local clone and parses git history for
  commits, LOC, contributors, and spec edits.
- `Verify authors: <repo>` — asks the GitHub commits API for verified
  email-to-login evidence for the same lookback window.

The collector also self-heals stale cache shapes:

- `discarded invalid clone cache: <repo>` means `.repos/<repo>` existed but was
  not a valid git checkout, for example a leftover symlink or partial failed
  clone.
- `discarded blobless clone cache: <repo>` means the old checkout used
  `remote.origin.partialclonefilter` and is being replaced with a clone that
  supports fast `git log --numstat`.
- `clone failed: <org>/<repo>: ...` and `fetch failed: <org>/<repo>: ...`
  include the last git error with the token redacted.

## Storage (SQLite)

The **SQLite** database `history/report.db` (override with `REPORT_DB`), managed
by `store.py`, is the **source of truth**. `collect.py` writes it; `render.py`
and `directory.py` read it (falling back to the `data.json` export only if the
DB is empty). Tables:

**Granular event tables (the source for period queries):**
- **`commits`** — one row per commit (date, author, repo, LOC, type, AI/bot flags).
- **`pull_request`** / **`issue`** — one row per PR / issue (created/merged dates,
  author, classification, bug/story + migration/bot flags).
- These make **any period a date-range query** — `store.aggregate(since, until)`
  powers both the presets and the custom-days endpoint. e.g. *Example Inc commits in a
  window*: `SELECT SUM(...) FROM commits c JOIN person p ON p.login=c.author_login
  WHERE p.company='Example Inc' AND c.committed_at BETWEEN ? AND ?`. Dates are stored
  UTC (`...Z`) so ranges compare cleanly.

**Dimensions:** **`person`** (login → name/company/emails/surviving-LOC) and
**`repo`** (key → classification/element/legacy/size), joined for company rollups.

**Other tables:**
- **`runs`** — the full `data.json` blob, one row per day (non-period report parts:
  repos inventory, traffic, provenance, bots, elements, identity). `render`/`directory`
  load the latest.
- **`traffic`** — raw per-repo/per-day clones & views (see below).
- **`snapshots`** — compact per-day aggregate behind the Trend tab.
- `person_runs` / `repo_runs` — legacy per-run aggregates, retained for cross-run
  SQL (superseded by the granular tables for period work).

The DB sits on the docker bind-mount (`.:/work`), so it persists across
container restarts; it is **git-ignored** (binary). Each run also exports the
durable history tables to **`history/*.jsonl`** and the latest payload to
**`data.json`** — text and diffable, so they are easy to back up and to inspect
(regenerated every run, so no drift). On first run an empty DB is **seeded** from any
existing `data.json` + `history/*.jsonl`.

> These exports are **git-ignored on purpose.** They describe your organisation:
> `snapshots.jsonl` carries per-company commit / LOC / headcount figures and
> `traffic.jsonl` carries repository names. An earlier internal version of this repo
> tracked them as a "text backup of the DB", which is exactly how internal metrics end
> up in a public git history. Back them up outside git.

### Layering: raw stays JSON, processed goes to SQLite

The split is deliberate. **Raw GitHub API responses — the "initial data" — stay
as JSON files** under `.cache/` (one file per request, TTL'd by
`cache_ttl_hours`; `NO_CACHE=1` bypasses). They are the auditable, untouched
source. Everything **processed or accumulated** — the run payload, normalised
people/repos, traffic series, trend snapshots — lives in SQLite. So `.cache/`
answers *"what did GitHub return"*, the DB answers *"what did we compute and how
did it change over time."*

## History & trend (snapshots)

The lookback window is a single point in time and can't show whether
contribution is rising or falling. To add the time dimension, every `collect.py`
run appends one compact dated row to **`history/snapshots.jsonl`** (per-company
commits/LOC/PRs/people + org totals). It is **idempotent per calendar day** — a
same-day re-run replaces that day's row — and only **complete** runs are
recorded (a rate-limited partial is skipped so it can't poison the trend). The
**Trend** tab plots these points; movement appears once ≥2 days accumulate.
Run the collector on a schedule (e.g. the weekly Action) and commit
`history/snapshots.jsonl` so the series persists across machines/runs.

### Raw traffic accumulation (perishable!)

GitHub's **Traffic API only returns the trailing 14 days** — older clone/view
counts are **unrecoverable** once they age out (unlike commits/LOC, which always
live in the clones). To preserve them, every run merges the raw per-repo/per-day
clone & view records into **`history/traffic.jsonl`**, keyed by `(repo, date)`
with the latest run winning (a day seen mid-progress is partial; a later run
completes it). **Run the collector at least once every 14 days** or there will
be permanent gaps. Because the data is genuinely unrecoverable, back this file up —
but **not into a public git repository**: it names your repositories. Both
`history/*.jsonl` files are git-ignored for that reason.

## Traffic — clones & page views

The **Traffic** panel shows GitHub traffic (last 14 days), with two distinct
signals badged by precision:

- **Page views / unique visitors** (`exact-human`) — people browsing the web UI.
  CI does not browse, so this is the cleaner human-usage signal. The panel also
  lists **popular paths** (what's viewed: PRs, issues, docs) and a **daily clone
  chart**.
- **Clones / unique cloners** (`heuristic`) — dominated by **CI/automation**:
  E2E and image-build workflows clone the repo per job, so each "unique cloner"
  is an ephemeral runner cloning ~14×. A daily spike means heavy CI that day,
  **not** more people. Read as volume/automation, never headcount. (An earlier
  "Use ÷ contrib" ratio was removed as misleading.)

Caveats common to all traffic:

- **Anonymous** — GitHub gives *how many*, never *who*. Forks & stars are the
  only per-person usage signals.
- **14-day window** — GitHub does not retain traffic longer; it's a snapshot,
  not windowed to `lookback_days`.
- **Needs push/admin** — `/traffic/*` returns `403` on repos the token can't
  push to, so a read-only token sees traffic for almost nothing; give the collector's
  token org-wide push (or use an org GitHub App with the traffic permission) to cover
  every repo. Repos without access are surfaced as "no
  traffic access yet" rather than silently dropped.

