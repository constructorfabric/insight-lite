"""Runtime-state isolation and the seeded fixture store, for EVERY test runner.

This used to live in conftest.py, which pytest loads before it imports any test
module — the only hook early enough, because server.py, directory.py, ghclient.py,
collect.py, identity.py and reportctl.py all bind their state paths at IMPORT time
from paths.data_path(). Whichever module imports one of them first fixes the target
for the whole session, so the redirect has to happen before any of that.

Why it moved out of conftest: conftest.py is a PYTEST file, and CI runs
`python -m unittest discover -s tests` (.github/workflows/tests.yml). unittest has no
conftest concept, so under the runner that actually gates merges there was no
redirect, no snapshot and no canary — tests read and wrote the checkout's own
history/report.db. The isolation looked present and did not run where it was claimed
to. Importing this module from BOTH tests/conftest.py and tests/__init__.py fixes
that: unittest imports the package before its test modules, pytest imports conftest,
and either way this runs first. Nothing here may import pytest.

The store: tests get a DETERMINISTIC seeded fixture DB, not a copy of whatever the
developer happens to have collected. Previously the session DB was a snapshot of the
checkout's history/report.db, because several tests (test_dashboard_json and the other
ambient-DB suites) resolve panels against real rows and an empty DB fails them. That
made the suite's result depend on the directory it was launched from: green in the main
checkout with 17 collected runs, red in every git worktree and red in CI, where
history/report.db is gitignored and cannot exist. 24 tests were failing that way, which
is how tests.yml could promise "a regression can't merge" while a fifth of the
dashboard surface never ran anywhere but one laptop. The fixture below satisfies the
same need — some rows, several people, companies, repos and weeks — reproducibly, so
the suite behaves identically everywhere.

Running against real collected data is still possible and still wins, via the
longstanding REPORT_DB override — but point it at a COPY, never at the live store:

    python -c "import sqlite3; s=sqlite3.connect('file:history/report.db?mode=ro',\\
    uri=True); d=sqlite3.connect('/tmp/realcopy.db'); s.backup(d)"
    REPORT_DB=/tmp/realcopy.db python -m pytest tests

The suite WRITES to whatever REPORT_DB names (test_dashboard_json seeds and drops a
dashboard), so aiming it at history/report.db mutates real collected data — the exact
class of accident that made the snapshot necessary in the first place. Using a copy is
the deliberate opt-in for "check the real data shapes"; either way it is no longer what
you get by accident from your working directory.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from hashlib import sha256
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Both runners normally put the repo root on sys.path (pytest via rootdir, unittest
# via the launch directory), but this module imports `store` to seed, and it must not
# depend on being launched from the right place to do it.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

STATE_DIR = Path(tempfile.mkdtemp(prefix="insight-tests-state-"))

# atexit, not pytest_sessionfinish: the cleanup has to belong to whoever created the
# directory, or the runner without the hook leaks one temp store (~0.5 MB with the
# seeded fixture) on every run — which is what happened when only conftest cleaned up
# and unittest started getting a temp dir of its own. ignore_errors because a test's
# leftover thread may still hold the DB open.
atexit.register(lambda: shutil.rmtree(STATE_DIR, ignore_errors=True))

os.environ["DATA_DIR"] = str(STATE_DIR)
# The per-item overrides win over the DATA_DIR-derived default (see paths.py), so
# redirecting DATA_DIR alone is not enough: a value exported in the developer's
# own shell would still aim writes at the checkout. (PEOPLE_YAML / CONFIG_LOCAL were
# pinned here too until the files they named stopped existing — nothing reads either
# env var now, so setting them would only imply they still steer something.)
os.environ["CLONE_DIR"] = str(STATE_DIR / ".repos")

# Fixed dates, never "today": a fixture that moves with the clock makes a failure
# depend on when it ran. Everything sits inside one span the "all" window covers; a
# test that needs rows in a recent window should seed its own rather than lean on this.
_WEEKS = 8
_PEOPLE = (("alice", "Alice A", "Acme"), ("bob", "Bob B", "Acme"),
           ("carol", "Carol C", "Globex"), ("dave", "Dave D", "Globex"))
_REPOS = (("o/platform-core", "platform", "Platform"), ("o/web-app", "app", "Apps"))


def _seed_fixture(db_path: Path) -> None:
    """Fill an empty store with a small, deterministic dataset.

    Sized by what the ambient-DB tests actually resolve, not by realism: several
    people across two companies (company breakdowns), two repos with different
    classifications (repo_type breakdowns), commits and pull requests spread over
    consecutive weeks (the trend tool's date axis and granularity picking), a mix of
    commit types and spec/AI flags (work_type and the AI split), and merged PRs with
    review counts (flow rates and cycle time).
    """
    os.environ["REPORT_DB"] = str(db_path)
    import store                      # imported here: REPORT_DB must be set first
    conn = store.connect()
    try:
        for login, name, company in _PEOPLE:
            conn.execute(
                "INSERT OR REPLACE INTO person (login, name, company, is_member, emails,"
                " surviving_code_human, surviving_code_ai, surviving_spec, cpt_lines,"
                " reviews_given, approvals_given, median_ttm_h, identity_confidence,"
                " identity_evidence) VALUES (?,?,?,1,?,900,300,80,400,12,9,5.0,"
                "'high','email match')",
                (login, name, company, f"{login}@example.com"))
        for key, classification, element in _REPOS:
            org, _, name = key.partition("/")
            conn.execute(
                "INSERT OR REPLACE INTO repo (key, org, name, classification, element,"
                " legacy_only, archived, stars, forks, code_loc, spec_loc)"
                " VALUES (?,?,?,?,?,0,0,3,1,5000,400)",
                (key, org, name, classification, element))
        n = 0
        for week in range(_WEEKS):
            for weekday in range(5):
                day = f"2026-{3 + week // 4:02d}-{1 + (week % 4) * 7 + weekday:02d}"
                for p_idx, (login, _, _) in enumerate(_PEOPLE):
                    for key, _, _ in _REPOS:
                        n += 1
                        conn.execute(
                            "INSERT OR REPLACE INTO commits (repo, sha, committed_at,"
                            " author_email, author_login, additions, deletions,"
                            " meaningful_additions, meaningful_deletions, is_spec,"
                            " commit_type, ai_marked, ai_loc, ai_tools, is_bot, title)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
                            (key, f"sha{n:05d}", f"{day}T1{p_idx}:00:00Z",
                             f"{login}@example.com", login, 40 + n % 30, 5,
                             30 + n % 20, 3, int(n % 7 == 0),
                             ("feat", "fix", "docs")[n % 3], int(n % 5 == 0), 10,
                             "copilot" if n % 5 == 0 else "", f"commit {n}"))
                        conn.execute(
                            "INSERT OR REPLACE INTO pull_request (repo, number, org,"
                            " author_login, created_at, merged_at, review_requested_at,"
                            " classification, is_migration, is_bot, state, closed_at,"
                            " additions, deletions, changed_files, review_count,"
                            " comment_count, is_revert, is_draft, title)"
                            " VALUES (?,?,?,?,?,?,?,?,0,0,'closed',?,?,?,?,?,?,0,0,?)",
                            (key, n, "o", login, f"{day}T09:00:00Z",
                             f"{day}T15:00:00Z", f"{day}T10:00:00Z",
                             ("feature", "bug")[n % 2], f"{day}T15:00:00Z",
                             50 + n % 40, 8, 3, 1 + n % 3, 2, f"pr {n}"))
        conn.execute(
            "INSERT OR REPLACE INTO runs (date, generated_at, lookback_days, org, payload)"
            " VALUES ('2026-04-28', '2026-04-28T00:00:00Z', 30, 'o', '{}')")
        conn.commit()
    finally:
        conn.close()


def _install_store() -> None:
    """Point REPORT_DB at a freshly seeded per-session fixture DB.

    An explicitly exported REPORT_DB wins untouched — that is the opt-in for running
    against real collected data, and the path test_person_api's REAL_DB reads."""
    if "REPORT_DB" in os.environ:
        return
    session_db = STATE_DIR / "history" / "report.db"
    session_db.parent.mkdir(parents=True, exist_ok=True)
    _seed_fixture(session_db)


_install_store()


# Runtime-state paths that live inside the checkout. history/report.db is
# deliberately excluded: an explicitly exported REPORT_DB may legitimately write it.
STATE_PATHS = (
    "people.yaml", "config.local.yaml", "identity_suggestions.yaml",
    "identity-editor.html", "config-editor.html", "other-people.md",
    "data.json", "report.html",
    "history/people", "history/config", "history/snapshots.jsonl",
    "history/traffic.jsonl", "exports", ".runtime", ".cache", ".repos",
)
# Clones and the API cache hold tens of thousands of files in a working checkout —
# walk only their top level, but stamp each entry's mtime, which is enough to see
# a write one level deeper (.cache/api/<hash>.json bumps .cache/api).
_SHALLOW = {".cache", ".repos"}


def _stamp(path: Path, rel: str) -> str:
    """Size + mtime + content digest. mtime matters: the 2026-07-28 leak rewrote
    people.yaml with bytes identical to the committed fixture, so a content-only
    stamp would have called that run clean."""
    if not path.exists():
        return "absent"
    st = path.stat()
    if path.is_dir():
        if rel in _SHALLOW:
            entries = sorted(f"{c.name}:{c.stat().st_mtime_ns}" for c in path.iterdir())
        else:
            entries = sorted(str(c.relative_to(path)) for c in path.rglob("*"))
        digest = sha256("\n".join(entries).encode()).hexdigest()[:16]
        return f"dir:{len(entries)}:{st.st_mtime_ns}:{digest}"
    return f"file:{st.st_size}:{st.st_mtime_ns}:{sha256(path.read_bytes()).hexdigest()[:16]}"


def checkout_state_fingerprint() -> dict[str, str]:
    return {rel: _stamp(REPO_ROOT / rel, rel) for rel in STATE_PATHS}


_AT_START = checkout_state_fingerprint()


def changed_since_start() -> dict[str, tuple[str, str]]:
    now = checkout_state_fingerprint()
    return {k: (_AT_START[k], v) for k, v in now.items() if _AT_START[k] != v}


def repin_data_dir() -> None:
    """test_paths.py reloads paths with DATA_DIR stripped from the environment to
    assert its documented default. A reload done inside the env patch leaves
    paths.DATA_DIR aimed at the checkout for the whole session, so every module
    imported later binds its state paths there — that is how reportctl.EXPORTS ended
    up under the checkout. Callers re-pin after each test so one reload cannot unpick
    the isolation for everything that follows."""
    import paths
    if paths.DATA_DIR != STATE_DIR:
        paths.DATA_DIR = STATE_DIR
