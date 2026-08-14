"""A pillar we never collected must not be charged to the person as a zero.

The score is a weighted mean of pillar percentiles, and a pillar missing for ONE person
used to contribute 0 to it. For engagement or craft that is the point — "you opened no
pull requests" is a fact about the person. For flow it is not: its inputs are timeline
events and Projects-board snapshots, which exist per REPOSITORY. Measured on the 30d
window of 2026-08-14, the board covers 25 of 34 scored people and the rank-1 person's
repository has zero rows on it, so "no flow reading" says where somebody works, not how
well. Charging 35% of a score for that measures the collector.

Flow is therefore renormalised away for a person with no reading: its weight goes to the
pillars that do have one. On production this moved exactly one person — somebody with 2
owned tracked items against a minimum of 3, who went 27 → 40 — which is the shape of the
bug: rare, invisible, and entirely our doing.

The team-level rule (_SCORE_PILLAR_COVERAGE, which drops a pillar for EVERYONE when
almost nobody has it) is unchanged and still tested elsewhere; this is its per-person
counterpart.
"""
import contextlib
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

SINCE, UNTIL = "2026-06-01", "2026-07-01"

#: Flow reads board movement, and the default taxonomy maps no statuses — without this
#: every status resolves to "other", which has no position, so nothing is a direction.
_STAGES = {"Backlog": "backlog", "In progress": "in_progress", "Done": "done"}


def _stage(_cfg, raw):
    return _STAGES.get(raw, "other")


@contextlib.contextmanager
def _store():
    import semantic
    with TemporaryDirectory() as tmp:
        with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}), \
             patch.object(semantic, "stage_for", _stage):
            import store
            conn = store.connect()
            yield store, conn
            conn.close()


def _work(conn, login, *, prs=6, peer_reviews=1, base=0, flow_items=6, bounces=0):
    """One person with enough activity to be scored, plus `flow_items` items that MOVE on
    the board — `bounces` of them backward, the rest forward. flow_items=0 leaves them
    with no flow reading at all, which is the case under test."""
    conn.execute("INSERT OR IGNORE INTO person (login, name) VALUES (?,?)",
                 (login, login.title()))
    conn.execute("INSERT OR IGNORE INTO person (login, name) VALUES ('reviewer','R')")
    for i in range(6):
        conn.execute(
            "INSERT INTO commits (repo, sha, committed_at, author_login, additions, "
            "meaningful_additions, is_spec, ai_marked, is_bot) "
            "VALUES ('o/r', ?, ?, ?, 10, 8, 0, 0, 0)",
            (f"{login}{i}", f"2026-06-{10 + i:02d}T00:00:00Z", login))
    for i in range(prs):
        num, day = base + i, f"2026-06-{10 + i % 18:02d}"
        conn.execute(
            "INSERT INTO pull_request (repo, number, org, author_login, created_at, "
            "merged_at, changed_files, review_count, is_revert, is_bot, is_migration) "
            "VALUES ('o/r', ?, 'o', ?, ?, ?, 3, 0, 0, 0, 0)",
            (num, login, f"{day}T00:00:00Z", f"{day}T06:00:00Z"))
        for j in range(peer_reviews):
            conn.execute(
                "INSERT INTO review (repo, pr_number, reviewer_login, state, submitted_at) "
                "VALUES ('o/r', ?, 'reviewer', 'COMMENTED', ?)", (num, f"{day}T03:0{j}:00Z"))
        if i < flow_items:
            # two snapshots so the item MOVES: forward normally, backward for `bounces`
            a, b = (("Done", "In progress") if i < bounces
                    else ("Backlog", "In progress"))
            for d, status in (("2026-06-05", a), ("2026-06-06", b)):
                conn.execute(
                    "INSERT INTO work_item_status (taken_at, date, item_id, project, "
                    "item_type, repo, number, status_raw, title) "
                    "VALUES (?, ?, ?, 'o/1', 'pull_request', 'o/r', ?, ?, ?)",
                    (f"{d}T00:00:00Z", d, f"{login}-{num}", num, status, f"t{num}"))
    conn.commit()


def _board(store, conn):
    return store.developer_scores(conn, since=SINCE, until=UNTIL)


class UnmeasuredFlowTest(unittest.TestCase):
    def test_a_person_with_no_flow_reading_is_not_scored_on_flow(self):
        with _store() as (store, conn):
            _work(conn, "ann", flow_items=6, bounces=2)
            _work(conn, "bob", flow_items=6, bounces=1, base=100)
            _work(conn, "cat", flow_items=0, base=200)      # no tracked items at all
            row = _board(store, conn)["by_login"]["cat"]
            self.assertIsNone(row["pillars"]["flow"])
            self.assertEqual(row["weight_gaps"], ["flow"])
            self.assertNotIn("flow", row["scored_on"])

    def test_the_missing_pillar_does_not_drag_the_score_to_zero(self):
        """The bug: flow contributed 0 × 35% for somebody we simply had no data on."""
        with _store() as (store, conn):
            _work(conn, "ann", flow_items=6, bounces=2)
            _work(conn, "bob", flow_items=6, bounces=1, base=100)
            _work(conn, "cat", flow_items=0, base=200)
            res = _board(store, conn)
            row = res["by_login"]["cat"]
            W = res["weights_raw"]
            others = [p for p in res["active_pillars"] if p != "flow"]
            expect = round(sum(W[p] * row["pillars"][p] for p in others)
                           / sum(W[p] for p in others))
            self.assertEqual(row["score"], expect,
                             "the score must be the mean of the pillars we could measure")

    def test_it_is_flow_only(self):
        """delivery and craft missing means "opened no PRs" — a fact about the person,
        which still costs them. Changing that is a separate decision."""
        with _store() as (store, conn):
            self.assertEqual(set(store._SCORE_GAP_PILLARS), {"flow"})

    def test_a_person_with_a_flow_reading_is_untouched(self):
        with _store() as (store, conn):
            _work(conn, "ann", flow_items=6, bounces=2)
            _work(conn, "bob", flow_items=6, bounces=1, base=100)
            row = _board(store, conn)["by_login"]["ann"]
            self.assertEqual(row["weight_gaps"], [])
            self.assertIn("flow", row["scored_on"])

    def test_the_pillar_points_still_add_up_to_the_score(self):
        """Largest-remainder rounding runs over the person's OWN denominator now; if it
        did not, the panel's "31 + 16 + 11 = 68" line would stop being true."""
        with _store() as (store, conn):
            _work(conn, "ann", flow_items=6, bounces=2)
            _work(conn, "bob", flow_items=6, bounces=1, base=100)
            _work(conn, "cat", flow_items=0, base=200)
            for row in _board(store, conn)["board"]:
                pts = sum(v for v in row["contributions"].values() if v is not None)
                self.assertEqual(pts, row["score"], f"{row['login']} does not add up")

    def test_a_dropped_pillar_carries_no_points_rather_than_zero_points(self):
        """None, not 0 — the client prints "—" for one and "0 pts" for the other, and
        "0 pts" is exactly the claim this change exists to stop making."""
        with _store() as (store, conn):
            _work(conn, "ann", flow_items=6, bounces=2)
            _work(conn, "bob", flow_items=6, bounces=1, base=100)
            _work(conn, "cat", flow_items=0, base=200)
            self.assertIsNone(_board(store, conn)["by_login"]["cat"]["contributions"]["flow"])


class RankGapTest(unittest.TestCase):
    """Points are a share of each person's OWN denominator. When one of the two was not
    scored on a pillar, its point totals are not comparable — and reading the absent one
    as 0 produced, on the live panel: "Ahead of X by 13 pts — mostly Flow: they 0.14, X
    no flow data", about somebody whose score never included Flow and who lost nothing
    for it. The largest gap must be picked from the pillars they share."""

    def test_the_gap_is_not_blamed_on_a_pillar_one_of_them_is_not_scored_on(self):
        with _store() as (store, conn):
            _work(conn, "ann", flow_items=6, bounces=0)          # clean flow, ranks high
            _work(conn, "bob", flow_items=6, bounces=3, base=100)
            _work(conn, "cat", flow_items=0, base=200)           # no flow reading
            board = _board(store, conn)["board"]
            for row in board:
                ab = row.get("above")
                if ab is None or ab.get("pillar") is None:
                    continue
                self.assertIn(ab["pillar"], row["scored_on"],
                              f"{row['login']} is compared on a pillar it is not scored on")

    def test_compare_row_to_applies_the_same_restriction(self):
        with _store() as (store, conn):
            _work(conn, "ann", flow_items=6, bounces=0)
            _work(conn, "bob", flow_items=6, bounces=3, base=100)
            _work(conn, "cat", flow_items=0, base=200)
            res = _board(store, conn)
            active = res["active_pillars"]
            got = store.compare_row_to(res["by_login"]["ann"], res["by_login"]["cat"], active)
            self.assertNotEqual(got.get("pillar"), "flow",
                                "cat is not scored on flow, so it cannot explain the gap")

    def test_two_people_with_the_same_pillars_still_compare_on_all_of_them(self):
        with _store() as (store, conn):
            _work(conn, "ann", flow_items=6, bounces=0)
            _work(conn, "bob", flow_items=6, bounces=3, base=100)
            res = _board(store, conn)
            got = store.compare_row_to(res["by_login"]["ann"], res["by_login"]["bob"],
                                       res["active_pillars"])
            self.assertIsNotNone(got["pillar"])


class DeltaTest(unittest.TestCase):
    def test_the_counterfactual_renormalises_the_same_way(self):
        """score_delta ranks the previous drivers against this window. If it divided by
        the full weight while the real score divided by less, the difference would show
        up as a move nobody made."""
        with _store() as (store, conn):
            _work(conn, "ann", flow_items=6, bounces=2)
            _work(conn, "bob", flow_items=6, bounces=1, base=100)
            _work(conn, "cat", flow_items=0, base=200)
            cur = _board(store, conn)
            for lg in ("ann", "cat"):
                d = store.score_delta(cur, cur, lg)
                self.assertEqual((d["total"], d["team"], d["you"]), (0, 0, 0),
                                 f"{lg}: a window against itself cannot move")


if __name__ == "__main__":
    unittest.main()
