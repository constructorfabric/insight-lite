"""The board-snapshot reads, and the sharing that keeps them from happening twice.

work_item_status is the biggest table in the store — 88 daily snapshots of every
tracked item, 141k rows on the Constructor org — and three metric functions read it.
Two of them now accept a read to walk instead of doing their own (board_snapshot_rows),
and the third stopped reading rows at all (board_cfd counts in SQL). Each of those is a
performance change whose whole point is that the ANSWER does not move, which is the
kind of change that breaks quietly: a metric that is 40% off still renders.

So these tests are about equality, not about speed. Every one of them asserts that the
fast path agrees with the slow one, or that a hand-counted fixture comes back exactly.
"""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import semantic
import semantic_metrics as sm
import store

# The default taxonomy maps none of these, so every test patches stage_for with this.
STAGES = {"Backlog": "backlog", "In Progress": "in_progress", "QA": "qa",
          "Done": "done", "Released": "released"}


def _stage(_cfg, raw):
    return STAGES.get(raw, "other")


class BoardSnapshotFixture(unittest.TestCase):
    """Two items over four days, with one rewind and one same-day double snapshot."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        with patch.dict(os.environ, {"REPORT_DB": str(Path(self._tmp.name) / "t.db")}):
            self.conn = store.connect()
        self.conn.execute(
            "INSERT INTO pull_request (repo,number,author_login) VALUES ('o/r',7,'alice')")
        # item 7 walks Backlog -> In Progress -> QA -> back to In Progress (one rewind).
        # item 9 sits in Done throughout, in a DIFFERENT repo, so a repo-scoped read has
        # something to exclude.
        for date, raw7, raw9 in (("2026-06-01", "Backlog", "Done"),
                                 ("2026-06-02", "In Progress", "Done"),
                                 ("2026-06-03", "QA", "Done"),
                                 ("2026-06-04", "In Progress", "Done")):
            store.write_work_item_status(self.conn, date, [
                {"item_id": "IT7", "project": "P", "item_type": "PullRequest",
                 "repo": "o/r", "number": 7, "status_raw": raw7, "title": "widget"},
                {"item_id": "IT9", "project": "P", "item_type": "Issue",
                 "repo": "o/other", "number": 9, "status_raw": raw9, "title": "thing"},
            ])
        self.conn.commit()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.conn.close)

    def _second_snapshot_that_day(self, date, raw):
        """A later, PARTIAL snapshot on a day that already has one: only item 7 is in it.

        board_cfd counts the last snapshot of each day, so this is what proves the
        MAX(taken_at) half of its query. It also exposes an assumption the metric has
        always made and still makes — that a snapshot holds every tracked item. When one
        does not, the items missing from it are missing from that day's diagram. Verified
        against the pre-SQL implementation: same numbers, quirk included. The real
        collector writes every item per run, so it does not arise in practice."""
        self.conn.execute(
            "INSERT INTO work_item_status (taken_at, date, item_id, project, item_type, "
            "repo, number, status_raw, title) VALUES (?,?,?,?,?,?,?,?,?)",
            (f"{date}T18:00:00Z", date, "IT7", "P", "PullRequest", "o/r", 7, raw, "widget"))
        self.conn.commit()


class SharedReadTest(BoardSnapshotFixture):
    """board_snapshot_rows exists so two consumers stop reading the same table twice.
    Handing them the shared read must be indistinguishable from letting them read."""

    def test_rewind_scan_agrees_with_its_own_read(self):
        with patch.object(semantic, "stage_for", _stage):
            own = sm.board_rewind_scan(self.conn, None)
            shared = sm.board_rewind_scan(self.conn, None, sm.board_snapshot_rows(self.conn, None))
        self.assertEqual(own, shared)
        self.assertEqual(len(own["events"]), 1, "the QA -> In Progress move on 06-04")

    def test_stage_dwell_agrees_with_its_own_read(self):
        with patch.object(semantic, "stage_for", _stage):
            rows = sm.board_snapshot_rows(self.conn, None)
            self.assertEqual(sm.stage_dwell(self.conn, None, None, None),
                             sm.stage_dwell(self.conn, None, None, None, rows))

    def test_flow_report_agrees_with_its_own_read(self):
        with patch.object(semantic, "stage_for", _stage):
            rows = sm.board_snapshot_rows(self.conn, None)
            self.assertEqual(sm.flow_report(self.conn, None, None, None),
                             sm.flow_report(self.conn, None, None, None, None, rows))

    def test_rows_carry_every_field_both_consumers_read(self):
        # The read selects the UNION of what the two of them use, and `date` was dropped
        # from it because neither does — pinned so a future column trim is deliberate.
        row = sm.board_snapshot_rows(self.conn, None)[0]
        for field in ("taken_at", "updated_at", "item_id", "repo", "number",
                      "item_type", "status_raw", "title"):
            self.assertIn(field, row.keys(), field)

    def test_the_read_is_scoped_and_ordered(self):
        rows = sm.board_snapshot_rows(self.conn, ["o/r"])
        self.assertEqual({r["repo"] for r in rows}, {"o/r"}, "the repo filter applies")
        keys = [(r["item_id"], r["taken_at"]) for r in rows]
        self.assertEqual(keys, sorted(keys), "consumers walk sequences — order is load-bearing")

    def test_an_empty_scope_reads_nothing(self):
        self.assertEqual(sm.board_snapshot_rows(self.conn, []), [])


class BoardCfdTest(BoardSnapshotFixture):
    """board_cfd counts in SQL now (a GROUP BY where a Python loop used to be), so the
    cases that matter are the ones the SQL has to reproduce: last-snapshot-of-a-day, and
    the repo filter — which lives in TWO places in that query, the subquery picking the
    day's last snapshot and the outer count."""

    def test_counts_one_item_per_stage_per_day(self):
        with patch.object(semantic, "stage_for", _stage):
            cfd = sm.board_cfd(self.conn, None, None, None)
        self.assertEqual(cfd["dates"], ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"])
        by_key = {s["key"]: s["vals"] for s in cfd["series"]}
        self.assertEqual(by_key["done"], [1, 1, 1, 1], "item 9 sits in Done all four days")
        self.assertEqual(by_key["backlog"], [1, 0, 0, 0])
        self.assertEqual(by_key["qa"], [0, 0, 1, 0])
        self.assertEqual(by_key["in_progress"], [0, 1, 0, 1])

    def test_only_the_last_snapshot_of_a_day_counts(self):
        self._second_snapshot_that_day("2026-06-03", "Done")
        with patch.object(semantic, "stage_for", _stage):
            cfd = sm.board_cfd(self.conn, None, None, None)
        by_key = {s["key"]: s["vals"] for s in cfd["series"]}
        i = cfd["dates"].index("2026-06-03")
        # Item 7 moved to Done in the later snapshot, and item 9 — absent from it — is
        # not counted that day at all. See _second_snapshot_that_day on why that is the
        # metric's long-standing assumption rather than something this rewrite changed.
        self.assertEqual(by_key["done"][i], 1)
        # The superseded 09:00 snapshot was the fixture's only QA, so the band goes:
        # board_cfd emits nothing that is zero throughout.
        self.assertNotIn("qa", by_key)
        self.assertEqual(by_key["done"], [1, 1, 1, 1], "the other days are untouched")

    def test_a_repo_scope_narrows_both_halves_of_the_query(self):
        # If only the outer half were filtered, the subquery would still pick the day's
        # last snapshot from ALL repos and the counts would come out of a snapshot this
        # scope cannot see.
        with patch.object(semantic, "stage_for", _stage):
            cfd = sm.board_cfd(self.conn, ["o/r"], None, None)
        by_key = {s["key"]: s["vals"] for s in cfd["series"]}
        self.assertNotIn("done", by_key, "item 9 is in another repo")
        self.assertEqual(by_key["qa"], [0, 0, 1, 0])

    def test_an_empty_scope_has_no_data(self):
        with patch.object(semantic, "stage_for", _stage):
            cfd = sm.board_cfd(self.conn, [], None, None)
        self.assertFalse(cfd["has_data"])
        self.assertEqual(cfd["n_dates"], 0)

    def test_the_window_still_clips_the_dates(self):
        with patch.object(semantic, "stage_for", _stage):
            cfd = sm.board_cfd(self.conn, None, "2026-06-02", "2026-06-03")
        self.assertEqual(cfd["dates"], ["2026-06-02", "2026-06-03"])


class RepoFilterAliasTest(unittest.TestCase):
    """_repo_filter takes an alias so a joined query can scope on `w.repo`. Three call
    sites used to rewrite its output with str.replace, which tied the exact text of the
    fragment to code in other functions."""

    def test_alias_qualifies_the_column(self):
        self.assertEqual(sm._repo_filter(["a", "b"], "w"),
                         (" AND w.repo IN (?,?)", ("a", "b")))

    def test_no_alias_is_the_bare_column(self):
        self.assertEqual(sm._repo_filter(["a"]), (" AND repo IN (?)", ("a",)))

    def test_none_means_every_repo(self):
        self.assertEqual(sm._repo_filter(None, "w"), ("", ()))

    def test_an_empty_list_matches_nothing_and_needs_no_alias(self):
        # The old str.replace never matched this fragment and so passed it through by
        # luck; it stays valid in an aliased query because it names no column at all.
        self.assertEqual(sm._repo_filter([], "w"), (" AND 1=0", ()))

    def test_nobody_rewrites_the_fragment_any_more(self):
        src = (Path(__file__).resolve().parents[1] / "backend/semantic_metrics.py").read_text()
        self.assertNotIn('rf.replace(" AND repo IN"', src,
                         "pass an alias to _repo_filter instead of patching its output")


class IndexMigrationTest(unittest.TestCase):
    """(item_id, date) replaces (item_id): it serves the same prefix lookups and also the
    latest-snapshot join flow_metrics makes. The replacement is the fragile part — a
    CREATE INDEX IF NOT EXISTS that reused the old NAME would be a silent no-op on every
    database that already had it."""

    def test_connect_installs_the_composite_and_removes_the_old_one(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            with patch.dict(os.environ, {"REPORT_DB": str(db)}):
                conn = store.connect()
                # a database from before the change
                conn.execute("DROP INDEX IF EXISTS idx_wis_item_date")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_wis_item "
                             "ON work_item_status(item_id)")
                conn.commit()
                conn.close()
                conn = store.connect()          # one ordinary connect must migrate it
                names = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name='work_item_status'")}
                conn.close()
        self.assertIn("idx_wis_item_date", names)
        self.assertNotIn("idx_wis_item", names, "superseded — a composite covers its prefix")

    def test_the_latest_snapshot_join_uses_it(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                plan = " ".join(r[3] for r in conn.execute(
                    "EXPLAIN QUERY PLAN "
                    "SELECT w.repo FROM work_item_status w "
                    "JOIN (SELECT item_id, MAX(date) md FROM work_item_status "
                    "      GROUP BY item_id) x "
                    "ON w.item_id=x.item_id AND w.date=x.md"))
                conn.close()
        # The point of the pair: the join is a point lookup on both columns rather than a
        # scan of everything sharing a date (see the index's comment in store.py).
        self.assertIn("idx_wis_item_date", plan)
        self.assertIn("item_id=? AND date=?", plan)


if __name__ == "__main__":
    unittest.main()
