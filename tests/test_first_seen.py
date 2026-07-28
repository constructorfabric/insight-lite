"""Guards for commits.first_seen — the arrival stamp that makes report windows measurable.

Windowed queries filter on commits.committed_at, which is the git AUTHOR date, so a PR
that sat open for months and then merges on a merge- or rebase-strategy repo injects its
commits into windows that were already reported. first_seen records when a row actually
entered this DB instead.

The whole feature hinges on one thing the write path makes easy to get wrong: commits are
keyed (repo, sha) and re-collected every night, and store._replace DELETEs the window
before re-INSERTing it. A stamp applied naively would therefore be reset on every run and
the column would measure nothing. test_rewrite_keeps_the_original_stamp is the test that
protects that; the rest pin the NULL contract for rows that predate the column.
"""
import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import store

# The commits table exactly as it shipped before first_seen was added.
_LEGACY_COMMITS = """
CREATE TABLE commits (
    repo                 TEXT NOT NULL,
    sha                  TEXT NOT NULL,
    committed_at         TEXT,
    author_email         TEXT,
    author_login         TEXT,
    classification       TEXT,
    additions            INTEGER DEFAULT 0,
    deletions            INTEGER DEFAULT 0,
    meaningful_additions INTEGER DEFAULT 0,
    meaningful_deletions INTEGER DEFAULT 0,
    is_spec              INTEGER DEFAULT 0,
    commit_type          TEXT,
    ai_marked            INTEGER DEFAULT 0,
    ai_loc               INTEGER DEFAULT 0,
    ai_tools             TEXT DEFAULT '',
    is_bot               INTEGER DEFAULT 0,
    title                TEXT DEFAULT '',
    PRIMARY KEY (repo, sha)
);
"""


def _commit(sha, at, repo="o/r", login="alice"):
    return {"repo": repo, "sha": sha, "committed_at": at, "author_email": f"{login}@x",
            "author_login": login, "classification": "app", "additions": 1,
            "deletions": 0, "meaningful_additions": 1, "meaningful_deletions": 0,
            "is_spec": 0, "commit_type": "feat", "ai_marked": 0, "ai_loc": 0,
            "is_bot": 0}


def _seen(conn):
    return {r["sha"]: r["first_seen"] for r in
            conn.execute("SELECT sha, first_seen FROM commits")}


def _write_at(conn, clock, rows, since=None):
    """write_commits with the arrival clock pinned — _utc_iso() only has second
    resolution, so two real writes in one test would otherwise be indistinguishable
    and 'the stamp did not move' would pass for the wrong reason."""
    with patch.object(store, "_utc_iso", lambda: clock):
        return store.write_commits(conn, rows, since=since)


class FirstSeenWriteTest(unittest.TestCase):
    def _db(self, tmp):
        with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
            return store.connect()

    def test_fresh_row_is_stamped_with_now(self):
        with TemporaryDirectory() as tmp:
            conn = self._db(tmp)
            _write_at(conn, "2026-07-28T10:00:00Z",
                      [_commit("a", "2026-06-01T00:00:00Z")])
            self.assertEqual(_seen(conn), {"a": "2026-07-28T10:00:00Z"})

    def test_rewrite_keeps_the_original_stamp(self):
        """The test the feature depends on: a nightly re-collect re-writes every row in
        the window, and must not reset the arrival stamp while doing it."""
        with TemporaryDirectory() as tmp:
            conn = self._db(tmp)
            since = "2026-01-01T00:00:00Z"
            _write_at(conn, "2026-07-01T00:00:00Z",
                      [_commit("a", "2026-06-01T00:00:00Z")], since=since)
            # a later run re-collects 'a' unchanged and brings one genuinely new commit
            _write_at(conn, "2026-07-28T10:00:00Z",
                      [_commit("a", "2026-06-01T00:00:00Z"),
                       _commit("b", "2026-07-28T09:00:00Z")], since=since)
            self.assertEqual(_seen(conn), {"a": "2026-07-01T00:00:00Z",
                                           "b": "2026-07-28T10:00:00Z"})

    def test_rewrite_keeps_the_stamp_on_a_full_table_wipe(self):
        """since=None takes _replace's DELETE FROM commits branch (reindex / tests)."""
        with TemporaryDirectory() as tmp:
            conn = self._db(tmp)
            _write_at(conn, "2026-07-01T00:00:00Z", [_commit("a", "2026-06-01T00:00:00Z")])
            _write_at(conn, "2026-07-28T10:00:00Z", [_commit("a", "2026-06-01T00:00:00Z")])
            self.assertEqual(_seen(conn), {"a": "2026-07-01T00:00:00Z"})

    def test_row_outside_a_narrow_window_keeps_its_stamp(self):
        """The other clobber route: a row the window DELETE does not touch, but which
        the INSERT OR REPLACE overwrites anyway because it is still in the payload."""
        with TemporaryDirectory() as tmp:
            conn = self._db(tmp)
            _write_at(conn, "2026-07-01T00:00:00Z",
                      [_commit("old", "2024-01-01T00:00:00Z")],
                      since="2008-01-01T00:00:00Z")
            _write_at(conn, "2026-07-28T10:00:00Z",
                      [_commit("old", "2024-01-01T00:00:00Z")],
                      since="2026-07-01T00:00:00Z")
            self.assertEqual(_seen(conn), {"old": "2026-07-01T00:00:00Z"})

    def test_writer_columns_all_exist_on_a_fresh_db(self):
        with TemporaryDirectory() as tmp:
            conn = self._db(tmp)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(commits)")}
            self.assertIn("first_seen", cols)
            self.assertLessEqual(set(store.COMMIT_COLS), cols)

    def test_existing_windowed_counts_are_unaffected(self):
        """first_seen is additive: the commit count for a window is what it always was."""
        with TemporaryDirectory() as tmp:
            conn = self._db(tmp)
            _write_at(conn, "2026-07-28T10:00:00Z",
                      [_commit("a", "2026-06-01T00:00:00Z"),
                       _commit("b", "2026-06-02T00:00:00Z")])
            agg = store.aggregate(conn, "2026-01-01T00:00:00Z", "2026-12-31T00:00:00Z")
            self.assertEqual(agg["totals"]["commits"], 2)


class FirstSeenMigrationTest(unittest.TestCase):
    def _legacy_db(self, tmp, rows=(("o/r", "old", "2024-01-01T00:00:00Z"),)):
        path = Path(tmp) / "legacy.db"
        legacy = sqlite3.connect(path)
        legacy.executescript(_LEGACY_COMMITS)
        legacy.executemany("INSERT INTO commits (repo, sha, committed_at, author_login, "
                           "meaningful_additions) VALUES (?, ?, ?, 'alice', 7)", rows)
        legacy.commit()
        legacy.close()
        return path

    def test_column_is_added_and_rows_survive_reading_null(self):
        with TemporaryDirectory() as tmp:
            path = self._legacy_db(tmp)
            with patch.dict(os.environ, {"REPORT_DB": str(path)}):
                conn = store.connect()
            cols = {r[1] for r in conn.execute("PRAGMA table_info(commits)")}
            self.assertIn("first_seen", cols)
            row = conn.execute("SELECT committed_at, meaningful_additions, first_seen "
                               "FROM commits WHERE sha='old'").fetchone()
            self.assertEqual((row["committed_at"], row["meaningful_additions"]),
                             ("2024-01-01T00:00:00Z", 7))
            # NULL, not the migration's clock: this row's real arrival is unknown, and
            # inventing 'now' for it would fabricate a zero-back-dating measurement
            self.assertIsNone(row["first_seen"])

    def test_migration_is_idempotent(self):
        with TemporaryDirectory() as tmp:
            path = self._legacy_db(tmp)
            with patch.dict(os.environ, {"REPORT_DB": str(path)}):
                store.connect().close()
                conn = store.connect()
            self.assertEqual(
                conn.execute("SELECT COUNT(*) n FROM commits").fetchone()["n"], 1)
            self.assertIsNone(_seen(conn)["old"])

    def test_pre_existing_null_survives_the_next_collect(self):
        """A migrated row re-collected the same night must stay NULL, not be back-filled
        with 'now' — otherwise the migration's honesty is undone one run later."""
        with TemporaryDirectory() as tmp:
            path = self._legacy_db(tmp)
            with patch.dict(os.environ, {"REPORT_DB": str(path)}):
                conn = store.connect()
            _write_at(conn, "2026-07-28T10:00:00Z",
                      [_commit("old", "2024-01-01T00:00:00Z"),
                       _commit("new", "2026-07-28T09:00:00Z")],
                      since="2008-01-01T00:00:00Z")
            self.assertEqual(_seen(conn), {"old": None, "new": "2026-07-28T10:00:00Z"})


class BackdatingStatsTest(unittest.TestCase):
    def _db(self, tmp):
        with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
            return store.connect()

    def _fixture(self, conn):
        """o/a: one commit 2h late (normal), one exactly 10 days late (back-dated).
        o/b: one row with no stamp at all, standing in for a pre-migration row."""
        _write_at(conn, "2026-07-28T00:00:00Z",
                  [_commit("fresh", "2026-07-27T22:00:00Z", repo="o/a"),
                   _commit("late", "2026-07-18T00:00:00Z", repo="o/a")])
        conn.execute("INSERT INTO commits (repo, sha, committed_at, first_seen) "
                     "VALUES ('o/b', 'unstamped', '2026-07-20T00:00:00Z', NULL)")
        conn.commit()

    def test_lag_percentiles_and_coverage(self):
        with TemporaryDirectory() as tmp:
            conn = self._db(tmp)
            self._fixture(conn)
            s = store.backdating_stats(conn)
            self.assertEqual((s["measured"], s["unknown"]), (2, 1))
            self.assertEqual(s["coverage_pct"], 66.7)
            self.assertEqual((s["backdated"], s["backdated_pct"]), (1, 50.0))
            # lags are 2h and 240h → median 121h, p90/max the 10-day arrival
            self.assertEqual(s["median_lag_h"], 121.0)
            self.assertEqual((s["p90_lag_h"], s["max_lag_h"]), (240.0, 240.0))
            self.assertFalse(s["period_scoped"])
            self.assertEqual(s["days"], store.BACKDATE_DAYS)

    def test_per_repo_breakdown_separates_unstamped_rows(self):
        with TemporaryDirectory() as tmp:
            conn = self._db(tmp)
            self._fixture(conn)
            by_repo = {r["repo"]: r for r in store.backdating_stats(conn)["repos"]}
            self.assertEqual([r["repo"] for r in store.backdating_stats(conn)["repos"]],
                             ["o/a", "o/b"])          # most back-dated first
            self.assertEqual((by_repo["o/a"]["measured"], by_repo["o/a"]["unknown"]),
                             (2, 0))
            self.assertEqual(by_repo["o/a"]["backdated"], 1)
            self.assertEqual(by_repo["o/a"]["max_lag_h"], 240.0)
            # the unstamped repo contributes a coverage gap, never a zero lag
            self.assertEqual((by_repo["o/b"]["measured"], by_repo["o/b"]["unknown"]),
                             (0, 1))
            self.assertEqual(by_repo["o/b"]["backdated"], 0)
            self.assertIsNone(by_repo["o/b"]["median_lag_h"])

    def test_repo_slice_applies(self):
        with TemporaryDirectory() as tmp:
            conn = self._db(tmp)
            self._fixture(conn)
            s = store.backdating_stats(conn, repos=["o/a"])
            self.assertEqual((s["measured"], s["unknown"]), (2, 0))
            self.assertEqual([r["repo"] for r in s["repos"]], ["o/a"])
            self.assertEqual(store.backdating_stats(conn, repos=[])["measured"], 0)

    def test_threshold_is_a_parameter(self):
        with TemporaryDirectory() as tmp:
            conn = self._db(tmp)
            self._fixture(conn)
            # at 30 days nothing in the fixture counts as back-dated any more
            self.assertEqual(store.backdating_stats(conn, days=30)["backdated"], 0)

    def test_a_db_with_no_stamps_reports_no_measurement_not_zero_lag(self):
        """The honesty case: nothing collected since the migration must read measured=0,
        not a reassuring 0% back-dating."""
        with TemporaryDirectory() as tmp:
            conn = self._db(tmp)
            conn.execute("INSERT INTO commits (repo, sha, committed_at, first_seen) "
                         "VALUES ('o/a', 'x', '2026-07-01T00:00:00Z', NULL)")
            conn.commit()
            s = store.backdating_stats(conn)
            self.assertEqual((s["measured"], s["unknown"], s["coverage_pct"]),
                             (0, 1, 0.0))
            self.assertIsNone(s["median_lag_h"])
            self.assertIsNone(s["p90_lag_h"])

    def test_empty_db(self):
        with TemporaryDirectory() as tmp:
            s = store.backdating_stats(self._db(tmp))
            self.assertEqual((s["measured"], s["unknown"], s["repos"]), (0, 0, []))
            self.assertIsNone(s["max_lag_h"])

    def test_metrics_are_registered_against_the_real_function(self):
        import metrics_registry as mreg
        by_name = {m["name"]: m for m in mreg.all_metrics()}
        for name in ("commit_backdating", "commit_backdating_lag"):
            self.assertIn(name, by_name)
            self.assertEqual(by_name[name]["fn"], "store.backdating_stats")
            self.assertIn(by_name[name]["group"], {g for g, _ in mreg.GROUPS})
            # the coverage caveat has to be in the description, not just the code
            self.assertIn("first_seen", by_name[name]["desc"])


if __name__ == "__main__":
    unittest.main()
