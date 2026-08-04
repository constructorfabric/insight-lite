"""The board-snapshot reads, and the sharing that keeps them from happening twice.

work_item_status is the biggest table in the store — 99 daily snapshots of every
tracked item, 141k rows on the Constructor org — and three metric functions read it.
Two of them now accept a read to walk instead of doing their own (board_snapshot_rows),
the third stopped reading rows at all (board_cfd counts in SQL), and the read itself no
longer touches work_item_status: it takes the 3.3k rows where a status CHANGED from a
derived cache. Each of those is a performance change whose whole point is that the ANSWER
does not move, which is the kind of change that breaks quietly: a metric that is 40% off
still renders.

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
        self._db = str(Path(self._tmp.name) / "t.db")
        with patch.dict(os.environ, {"REPORT_DB": self._db}):
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


def _full_read(conn, repos=None):
    """What board_snapshot_rows used to be: every snapshot row, no cache involved.

    The derived read is only correct because the rows it drops cannot change an answer,
    so the tests below assert exactly that — the fast read and this one agree.
    """
    rf, rp = sm._repo_filter(repos)
    return conn.execute(
        "SELECT taken_at, updated_at, item_id, repo, number, item_type, status_raw, title "
        "FROM work_item_status WHERE status_raw IS NOT NULL" + rf +
        " ORDER BY item_id, taken_at", rp).fetchall()


class _CountingConn:
    """A connection that counts commits, for asserting how many transactions a write uses.

    sqlite3.Connection takes no instance attributes, so this delegates instead of patching.
    """

    def __init__(self, conn):
        self._conn = conn
        self.commits = 0

    def commit(self):
        self.commits += 1
        self._conn.commit()

    def __getattr__(self, name):
        return getattr(self._conn, name)


class DerivedKeyTableTest(BoardSnapshotFixture):
    """work_item_key / work_item_instant: a rebuildable cache of the rows that matter.

    The snapshots are a daily photograph, so nearly every row repeats the previous one's
    status. These tests pin the two halves that make dropping them safe — the cache holds
    the right subset, and it can never lag the table it derives from."""

    def test_the_cache_holds_changes_and_last_sightings_only(self):
        n = self.conn.execute("SELECT COUNT(*) FROM work_item_status").fetchone()[0]
        keys = [(r["item_id"], r["taken_at"][:10], r["status_raw"]) for r in self.conn.execute(
            "SELECT item_id, taken_at, status_raw FROM work_item_key "
            "ORDER BY item_id, taken_at")]
        self.assertEqual(n, 8, "two items over four days")
        self.assertEqual(keys, [
            # item 7 moves every day, so every row is a change
            ("IT7", "2026-06-01", "Backlog"),
            ("IT7", "2026-06-02", "In Progress"),
            ("IT7", "2026-06-03", "QA"),
            ("IT7", "2026-06-04", "In Progress"),
            # item 9 never moves: its first sighting, and its last
            ("IT9", "2026-06-01", "Done"),
            ("IT9", "2026-06-04", "Done"),
        ])

    def test_nothing_is_dropped_from_the_snapshots_themselves(self):
        # The whole promise of the cache: it is derived, and the photograph is intact.
        before = store.read_work_item_status(self.conn)
        store.refresh_work_item_key(self.conn)
        self.assertEqual(store.read_work_item_status(self.conn), before)

    def test_the_instants_are_every_snapshot_not_every_change(self):
        # 99 instants against 141k rows in production, and the reason they are a separate
        # read: "which days did we capture" is a property of the snapshot SET, and the row
        # list deliberately is not the whole set.
        self.assertEqual(sm.board_snapshot_instants(self.conn, None),
                         ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"])

    def test_an_instant_where_nothing_moved_is_still_an_instant(self):
        """The case that separates the two reads, and the reason both exist.

        On a quiet day no item changes status, so the day contributes no change row —
        and if it is also not the last day, no last-sighting row either. It is still a
        snapshot we took. Deriving the instants from the change rows loses it, and with
        it a column of the CFD and a day of n_dates."""
        for date in ("2026-06-05", "2026-06-06"):
            store.write_work_item_status(self.conn, date, [
                {"item_id": "IT7", "project": "P", "item_type": "PullRequest",
                 "repo": "o/r", "number": 7, "status_raw": "In Progress", "title": "widget"},
                {"item_id": "IT9", "project": "P", "item_type": "Issue",
                 "repo": "o/other", "number": 9, "status_raw": "Done", "title": "thing"},
            ])
        rows = sm.board_snapshot_rows(self.conn, None)
        self.assertNotIn("2026-06-05", {r["taken_at"] for r in rows},
                         "nothing moved and it is not the last day")
        self.assertIn("2026-06-05", sm.board_snapshot_instants(self.conn, None))
        with patch.object(semantic, "stage_for", _stage):
            self.assertEqual(sm.stage_dwell(self.conn, None, None, None)["n_dates"], 6)

    def test_a_second_snapshot_that_day_is_an_instant_too(self):
        self._second_snapshot_that_day("2026-06-03", "Done")
        store.refresh_work_item_key(self.conn)
        self.assertEqual(sm.board_snapshot_instants(self.conn, None),
                         ["2026-06-01", "2026-06-02", "2026-06-03",
                          "2026-06-03T18:00:00Z", "2026-06-04"])

    def test_the_derived_read_answers_what_the_full_read_answers(self):
        full = _full_read(self.conn)
        with patch.object(semantic, "stage_for", _stage):
            self.assertEqual(sm.board_rewind_scan(self.conn, None, full),
                             sm.board_rewind_scan(self.conn, None))
            self.assertEqual(sm.stage_dwell(self.conn, None, None, None, full),
                             sm.stage_dwell(self.conn, None, None, None))
            self.assertEqual(sm.flow_report(self.conn, None, None, None, None, full),
                             sm.flow_report(self.conn, None, None, None))

    def test_the_derived_read_answers_what_the_full_read_answers_when_scoped(self):
        # In production one item's repo changes mid-history, which puts a first-sighting
        # row in the scoped change set that the global one does not have. It moves no
        # metric — dwell skips the first observed run and a rewind needs a predecessor —
        # and this is the assertion that would catch it if that ever stopped being true.
        with patch.object(semantic, "stage_for", _stage):
            self.assertEqual(
                sm.stage_dwell(self.conn, ["o/r"], None, None, _full_read(self.conn, ["o/r"]),
                               sm.board_snapshot_instants(self.conn, ["o/r"])),
                sm.stage_dwell(self.conn, ["o/r"], None, None))

    def test_n_dates_counts_snapshots_not_change_rows(self):
        # Reading it off the row list instead reported 17 of 19 days on production, and
        # would have taken the "waiting now" lens with it: that asks whether an item's
        # newest row is the newest SNAPSHOT, which an item that never moves fails.
        with patch.object(semantic, "stage_for", _stage):
            dwell = sm.stage_dwell(self.conn, None, None, None)
            full = sm.stage_dwell(self.conn, None, None, None, _full_read(self.conn))
        self.assertEqual(dwell["n_dates"], 4)
        self.assertEqual(dwell["stages"], full["stages"])

    def test_current_items_are_still_seen_as_waiting(self):
        # item 9 sits in Done (terminal, excluded), so give item 7 a non-terminal present.
        with patch.object(semantic, "stage_for", _stage):
            dwell = sm.stage_dwell(self.conn, None, None, None)
        current = {s["key"]: s["n_current"] for s in dwell["stages"]}
        self.assertEqual(current.get("in_progress"), 1, "item 7's latest stage")

    def test_a_rerun_of_one_instant_refreshes_the_cache(self):
        # The case a cheap staleness marker misses: same instant, same row count, same
        # newest timestamp — different statuses. Only rebuilding on the writer catches it.
        before = self.conn.execute(
            "SELECT COUNT(*), MAX(taken_at) FROM work_item_status").fetchone()
        store.write_work_item_status(self.conn, "2026-06-04", [
            {"item_id": "IT7", "project": "P", "item_type": "PullRequest",
             "repo": "o/r", "number": 7, "status_raw": "Done", "title": "widget"},
            {"item_id": "IT9", "project": "P", "item_type": "Issue",
             "repo": "o/other", "number": 9, "status_raw": "Done", "title": "thing"},
        ])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*), MAX(taken_at) FROM work_item_status").fetchone(), before,
            "nothing a count-or-max check could notice")
        self.assertEqual(self.conn.execute(
            "SELECT status_raw FROM work_item_key WHERE item_id='IT7' "
            "ORDER BY taken_at DESC LIMIT 1").fetchone()[0], "Done")
        with patch.object(semantic, "stage_for", _stage):
            self.assertEqual(sm.board_rewind_scan(self.conn, None)["events"], [],
                             "QA -> In Progress became QA -> Done: no rewind left")

    def test_the_status_write_and_the_cache_rebuild_are_one_transaction(self):
        """The cache cannot lag the table only if both land at once.

        Committing the statuses and then the rebuild left a window where a reader on
        another connection saw the new statuses against the old cache — and _board_key
        cannot notice, because its guard rebuilds an EMPTY cache and a stale one is merely
        behind. Under WAL that reader does not block, so the window is observable. Counting
        commits is the deterministic way to pin this; racing two connections is not."""
        counted = _CountingConn(self.conn)
        store.write_work_item_status(counted, "2026-06-05", [
            {"item_id": "IT7", "project": "P", "item_type": "PullRequest",
             "repo": "o/r", "number": 7, "status_raw": "QA", "title": "widget"},
        ])
        self.assertEqual(counted.commits, 1,
                         "two commits means a window where the cache disagrees")
        self.assertEqual(self.conn.execute(
            "SELECT status_raw FROM work_item_key WHERE item_id='IT7' "
            "ORDER BY taken_at DESC LIMIT 1").fetchone()[0], "QA",
            "and the one transaction still leaves the cache current")

    def test_the_lazy_rebuild_still_commits_on_its_own(self):
        """refresh_work_item_key defaults to committing, because _board_key calls it with
        no write of its own to bundle with. A second connection is the proof: an uncommitted
        rebuild would be invisible there, and the next reader would rebuild all over again."""
        self.conn.execute("DELETE FROM work_item_key")
        self.conn.execute("DELETE FROM work_item_instant")
        self.conn.commit()
        store.refresh_work_item_key(self.conn)
        with patch.dict(os.environ, {"REPORT_DB": self._db}):
            other = store.connect()
        self.addCleanup(other.close)
        self.assertEqual(other.execute("SELECT COUNT(*) FROM work_item_key").fetchone()[0], 6,
                         "visible from another connection, so it was committed")

    def test_a_database_with_no_cache_builds_one_on_read(self):
        # Databases written before the cache existed, and any database whose cache was
        # deleted — which must always be safe, because it is derived.
        self.conn.execute("DELETE FROM work_item_key")
        self.conn.execute("DELETE FROM work_item_instant")
        self.conn.commit()
        with patch.object(semantic, "stage_for", _stage):
            rows = sm.board_snapshot_rows(self.conn, None)
            self.assertEqual(len(rows), 6, "rebuilt on the way past")
            self.assertEqual(len(sm.board_snapshot_instants(self.conn, None)), 4)
            self.assertEqual(sm.stage_dwell(self.conn, None, None, None, _full_read(self.conn)),
                             sm.stage_dwell(self.conn, None, None, None))

    def test_every_row_says_which_of_the_three_kinds_it_is(self):
        """prev_status_raw makes the cache the board's transition log, and makes a
        last-sighting row distinguishable from a real move without looking at neighbours."""
        rows = self.conn.execute(
            "SELECT item_id, taken_at, prev_status_raw, status_raw FROM work_item_key "
            "ORDER BY item_id, taken_at").fetchall()
        kind = {(r["item_id"], r["taken_at"]):
                ("first" if r["prev_status_raw"] is None else
                 "moved" if r["prev_status_raw"] != r["status_raw"] else "still")
                for r in rows}
        self.assertEqual(kind, {
            ("IT7", "2026-06-01"): "first",
            ("IT7", "2026-06-02"): "moved",
            ("IT7", "2026-06-03"): "moved",
            ("IT7", "2026-06-04"): "moved",
            ("IT9", "2026-06-01"): "first",
            ("IT9", "2026-06-04"): "still",   # never moved; here as its last sighting
        })
        moves = [(r["prev_status_raw"], r["status_raw"]) for r in rows
                 if r["prev_status_raw"] and r["prev_status_raw"] != r["status_raw"]]
        self.assertEqual(moves, [("Backlog", "In Progress"), ("In Progress", "QA"),
                                 ("QA", "In Progress")])

    def test_a_cache_from_before_prev_status_is_migrated_and_refilled(self):
        # Emptying it IS the migration: it is derived, so the next read rebuilds it.
        self.conn.execute("DROP TABLE work_item_key")
        self.conn.execute(
            "CREATE TABLE work_item_key (taken_at TEXT NOT NULL, updated_at TEXT, "
            "item_id TEXT NOT NULL, repo TEXT, number INTEGER, item_type TEXT, "
            "status_raw TEXT, title TEXT)")
        self.conn.execute(
            "INSERT INTO work_item_key (taken_at, item_id, status_raw) "
            "VALUES ('2026-06-01','IT7','Backlog')")   # non-empty, so no lazy rebuild yet
        self.conn.commit()
        db = Path(self.conn.execute("PRAGMA database_list").fetchone()[2])
        self.conn.close()
        with patch.dict(os.environ, {"REPORT_DB": str(db)}):
            self.conn = store.connect()
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(work_item_key)")]
        self.assertIn("prev_status_raw", cols)
        self.assertEqual(len(sm.board_snapshot_rows(self.conn, None)), 6, "refilled on read")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM work_item_key WHERE prev_status_raw IS NOT NULL"
        ).fetchone()[0], 4, "three moves and item 9's unchanged last sighting")

    def test_a_database_with_no_snapshots_does_not_thrash(self):
        # An empty cache is indistinguishable from a stale one, so the lazy build has to
        # stop somewhere: no snapshots, nothing to build, and no rebuild attempt per read.
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "e.db")}):
                conn = store.connect()
            with patch.object(store, "refresh_work_item_key") as refresh:
                self.assertEqual(sm.board_snapshot_rows(conn, None), [])
                self.assertEqual(sm.board_snapshot_instants(conn, None), [])
            refresh.assert_not_called()
            conn.close()


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
