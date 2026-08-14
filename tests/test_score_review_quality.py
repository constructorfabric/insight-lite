"""The developer score used to reward NOT being reviewed. These pin the v0.3 fix.

Measured on production, 30d window of 2026-08-13: the share of a person's merged PRs
that nobody reviewed correlated +0.59 with their score — a stronger predictor than
volume (+0.50). The board's top three were 100%, 77% and 100% unreviewed. Three
mechanics produced that, and each one is pinned below:

  * `rounds` averaged review_count over merged PRs INCLUDING the zeros, and the signal
    is lower-is-better, so "nobody opened it" ranked as the cleanest work on the board;
  * nothing measured whether the work was reviewed at all, so the absence was free;
  * every ratio was ranked at face value, so a ratio over 3 PRs outranked one over 800.

And a fourth, found while fixing those: "reviewed" was read from
pull_request.review_count, which is GitHub's reviews.totalCount — it counts CodeRabbit
and friends (config bot_logins) and the author's own reviews. 36 of 520 merged PRs
looked reviewed with no human anywhere near them, and 15 more had only the author. The
`review` table is bot-free by collection, so peer reviews are counted from there.

That correction mostly HELPED high-volume people: the review rounds the score was
charging them for were largely automated: one high-volume contributor went from 5.25
review rounds to 1.41 and from rank 8 to rank 2.
test_review_rounds_do_not_punish_a_well_discussed_pr is that property, pinned.
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


@contextlib.contextmanager
def _store():
    with TemporaryDirectory() as tmp:
        with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
            import store
            conn = store.connect()
            yield store, conn
            conn.close()


def _person(conn, login):
    conn.execute("INSERT OR IGNORE INTO person (login, name) VALUES (?,?)",
                 (login, login.title()))


def _work(conn, login, *, commits=6, prs=6, merged=True, review_count=0,
          peer_reviews=0, self_reviews=0, files=3, base=0):
    """One person's window: `commits` commits and `prs` PRs, each PR carrying
    `peer_reviews` rows from a reviewer who is not the author (and `self_reviews` from
    the author, which must never count). `review_count` is GitHub's inflated column and
    is deliberately set independently of the review rows, because the whole point is
    that the score no longer reads it."""
    _person(conn, login)
    for i in range(commits):
        conn.execute(
            "INSERT INTO commits (repo, sha, committed_at, author_login, additions, "
            "meaningful_additions, is_spec, ai_marked, is_bot) "
            "VALUES ('o/r', ?, ?, ?, 10, 8, 0, 0, 0)",
            (f"{login}{i}", f"2026-06-{10 + i % 18:02d}T00:00:00Z", login))
    for i in range(prs):
        num = base + i
        day = f"2026-06-{10 + i % 18:02d}"
        conn.execute(
            "INSERT INTO pull_request (repo, number, org, author_login, created_at, "
            "merged_at, changed_files, review_count, is_revert, is_bot, is_migration) "
            "VALUES ('o/r', ?, 'o', ?, ?, ?, ?, ?, 0, 0, 0)",
            (num, login, f"{day}T00:00:00Z",
             f"{day}T06:00:00Z" if merged else None, files, review_count))
        for j in range(peer_reviews):
            _person(conn, "reviewer")
            conn.execute(
                "INSERT INTO review (repo, pr_number, reviewer_login, state, submitted_at) "
                "VALUES ('o/r', ?, 'reviewer', 'COMMENTED', ?)",
                (num, f"{day}T03:0{j}:00Z"))
        for j in range(self_reviews):
            conn.execute(
                "INSERT INTO review (repo, pr_number, reviewer_login, state, submitted_at) "
                "VALUES ('o/r', ?, ?, 'COMMENTED', ?)",
                (num, login, f"{day}T04:0{j}:00Z"))
    conn.commit()


def _row(store, conn, login, **kw):
    return store.developer_scores(conn, since=SINCE, until=UNTIL,
                                  **kw)["by_login"][login]


class UnreviewedIsNotCraftTest(unittest.TestCase):
    def test_nobody_reviewing_you_leaves_rounds_unknown_not_zero(self):
        """0 rounds ranked as the best work on the board. It is not a reading at all."""
        with _store() as (store, conn):
            _work(conn, "solo", peer_reviews=0)
            self.assertIsNone(_row(store, conn, "solo")["drivers"]["rounds"])

    def test_a_reviewed_pr_still_reports_its_rounds(self):
        with _store() as (store, conn):
            _work(conn, "solo", peer_reviews=0)
            _work(conn, "pair", peer_reviews=2, base=100)
            self.assertEqual(_row(store, conn, "pair")["drivers"]["rounds"], 2.0)

    def test_reviewed_share_is_the_signal_that_accounts_for_the_absence(self):
        with _store() as (store, conn):
            _work(conn, "solo", peer_reviews=0)
            _work(conn, "pair", peer_reviews=2, base=100)
            self.assertEqual(_row(store, conn, "solo")["drivers"]["reviewed_share"], 0.0)
            self.assertEqual(_row(store, conn, "pair")["drivers"]["reviewed_share"], 1.0)

    def test_the_production_defect_itself(self):
        """Two people, identical output; one is reviewed, one is not. The unreviewed one
        must not win on craft. This is the whole bug in four lines."""
        with _store() as (store, conn):
            _work(conn, "solo", peer_reviews=0)
            _work(conn, "pair", peer_reviews=2, base=100)
            solo = _row(store, conn, "solo")["pillars"]["craft"]
            pair = _row(store, conn, "pair")["pillars"]["craft"]
            self.assertLess(solo, pair,
                            "being unreviewed outscored being reviewed — the v0.2 defect")


class PeerReviewOnlyTest(unittest.TestCase):
    def test_github_review_count_alone_does_not_make_work_reviewed(self):
        """review_count counts bots. `review` is bot-free, so it is what decides."""
        with _store() as (store, conn):
            _work(conn, "botted", review_count=7, peer_reviews=0)
            d = _row(store, conn, "botted")["drivers"]
            self.assertEqual(d["reviewed_share"], 0.0,
                             "a CodeRabbit review is not somebody looking at your work")
            self.assertIsNone(d["rounds"])

    def test_reviewing_your_own_pr_does_not_count(self):
        with _store() as (store, conn):
            _work(conn, "selfie", peer_reviews=0, self_reviews=3, review_count=3)
            d = _row(store, conn, "selfie")["drivers"]
            self.assertEqual(d["reviewed_share"], 0.0)
            self.assertIsNone(d["rounds"])

    def test_a_peer_review_counts_even_when_review_count_disagrees(self):
        with _store() as (store, conn):
            _work(conn, "real", peer_reviews=1, review_count=0)
            d = _row(store, conn, "real")["drivers"]
            self.assertEqual(d["reviewed_share"], 1.0)
            self.assertEqual(d["rounds"], 1.0)

    def test_review_rounds_do_not_punish_a_well_discussed_pr(self):
        """The balance property: somebody whose work draws discussion must not be ranked
        below somebody whose work draws none, once the automated noise is out. Both are
        peer-reviewed here; `discussed` simply gets more of it, and the gap that remains
        is the honest one — not the 5.25-vs-1.41 inflation bots were adding."""
        with _store() as (store, conn):
            _work(conn, "quiet", peer_reviews=1)
            _work(conn, "discussed", peer_reviews=3, base=100)
            _work(conn, "unreviewed", peer_reviews=0, base=200)
            got = {lg: _row(store, conn, lg)["pillars"]["craft"]
                   for lg in ("quiet", "discussed", "unreviewed")}
            self.assertGreater(got["discussed"], got["unreviewed"],
                               "discussion must beat no review at all")


class ShrinkageTest(unittest.TestCase):
    def test_a_ratio_over_few_items_is_pulled_toward_the_team(self):
        """3 PRs of a perfect ratio must not outrank 60 PRs of a good one."""
        with _store() as (store, conn):
            _work(conn, "tiny", commits=6, prs=3, peer_reviews=1, files=1)
            _work(conn, "bulk", commits=60, prs=60, peer_reviews=1, files=2, base=100)
            _work(conn, "mid", commits=20, prs=20, peer_reviews=2, files=6, base=300)
            res = store.developer_scores(conn, since=SINCE, until=UNTIL)
            tiny = res["by_login"]["tiny"]
            self.assertEqual(tiny["drivers"]["size"], 1,
                             "the DRIVER stays raw — it is what the person did")
            self.assertEqual(tiny["obs"]["size"], 3, "3 observations behind that ratio")
            self.assertGreater(res["shrink"]["k"], 0)

    def test_shrinkage_moves_the_ranked_value_but_not_the_reported_one(self):
        with _store() as (store, conn):
            _work(conn, "tiny", commits=6, prs=3, peer_reviews=1, files=1)
            _work(conn, "bulk", commits=60, prs=60, peer_reviews=1, files=9, base=100)
            res = store.developer_scores(conn, since=SINCE, until=UNTIL)
            med = res["shrink"]["medians"]["size"]
            k = res["shrink"]["k"]
            shrunk = store._shrink(1, 3, med, k)
            self.assertNotEqual(shrunk, 1, "a 3-observation ratio must be pulled")
            self.assertTrue(1 < shrunk < med, f"pulled toward {med}, not past it")

    def test_team_medians_stay_raw(self):
        """They are printed next to the person's raw driver; shrinking one side lies."""
        with _store() as (store, conn):
            _work(conn, "a", commits=6, prs=3, peer_reviews=1, files=1)
            _work(conn, "b", commits=60, prs=60, peer_reviews=1, files=9, base=100)
            res = store.developer_scores(conn, since=SINCE, until=UNTIL)
            self.assertIn(res["team_medians"]["size"], (1, 9, 5.0),
                          "a median of the two RAW values, not of shrunk ones")

    def test_counts_are_never_shrunk(self):
        """3 commits is 3 commits — there is nothing to infer."""
        with _store() as (store, conn):
            _work(conn, "a", commits=6, prs=6, peer_reviews=1)
            self.assertNotIn("commits", store._SCORE_RATIO_SIGNALS)
            self.assertNotIn("loc", store._SCORE_RATIO_SIGNALS)

    def test_k_zero_disables_it(self):
        self.assertEqual(__import__("store")._shrink(4.0, 2, 10.0, 0), 4.0)

    def test_no_median_leaves_the_value_alone(self):
        """An empty distribution must not turn a real value into None or a crash."""
        self.assertEqual(__import__("store")._shrink(4.0, 2, None, 10), 4.0)


class DeltaConsistencyTest(unittest.TestCase):
    def test_the_board_carries_the_observation_counts_a_delta_needs(self):
        with _store() as (store, conn):
            _work(conn, "a", peer_reviews=1)
            row = _row(store, conn, "a")
            self.assertEqual(row["obs"]["merge_rate"], 6)
            self.assertEqual(row["obs"]["reviewed_share"], 6)

    def test_a_delta_shrinks_the_previous_window_the_same_way(self):
        """score_delta ranks the PREVIOUS drivers against THIS window's distribution. If
        it fed them in raw while the distribution is shrunk, the mismatch would land on
        the person as "you moved" — the exact confusion the split exists to prevent."""
        with _store() as (store, conn):
            _work(conn, "a", commits=6, prs=3, peer_reviews=1, files=1)
            _work(conn, "b", commits=40, prs=40, peer_reviews=2, files=8, base=100)
            cur = store.developer_scores(conn, since=SINCE, until=UNTIL)
            d = store.score_delta(cur, cur, "a")
            self.assertEqual(d["total"], 0, "same window against itself cannot move")
            self.assertEqual(d["team"], 0)
            self.assertEqual(d["you"], 0)

    def test_a_payload_without_obs_does_not_shrink_to_the_median(self):
        """Pre-v0.3 payloads have no `obs`. Treating that as n=0 would shrink every
        driver all the way to the median and report a fictional move."""
        with _store() as (store, conn):
            _work(conn, "a", commits=6, prs=3, peer_reviews=1, files=1)
            _work(conn, "b", commits=40, prs=40, peer_reviews=2, files=8, base=100)
            cur = store.developer_scores(conn, since=SINCE, until=UNTIL)
            old = {**cur, "by_login": {lg: {**r, "obs": None}
                                       for lg, r in cur["by_login"].items()}}
            self.assertEqual(store.score_delta(cur, old, "a")["total"], 0)


class SignalWiringTest(unittest.TestCase):
    def test_every_signal_has_a_label(self):
        import store
        for _pillar, key, _dir in store._SCORE_SIGNALS:
            self.assertIn(key, store._SCORE_SIGNAL_META, f"{key} has no label")

    def test_reviewed_share_reaches_the_ui_spec(self):
        import store
        spec = {s["key"]: s for s in store.score_signal_spec()}
        self.assertIn("reviewed_share", spec)
        self.assertTrue(spec["reviewed_share"]["higher_is_better"])
        self.assertEqual(spec["reviewed_share"]["pillar"], "craft")

    def test_the_craft_headline_metric_is_defined_for_the_unreviewed(self):
        """It explains rank gaps. `rounds` is None for them, so it cannot be the one."""
        with _store() as (store, conn):
            _work(conn, "solo", peer_reviews=0)
            _work(conn, "pair", peer_reviews=2, base=100)
            key = store._PILLAR_PRIMARY["craft"]["key"]
            self.assertIsNotNone(_row(store, conn, "solo")["drivers"][key])


#: Flow reads board movement; the default taxonomy maps no statuses, so without this
#: every status is "other", which has no position and therefore no direction.
_STAGES = {"Backlog": "backlog", "In progress": "in_progress", "Done": "done"}


def _board(conn, login, nums, status_a="Backlog", status_b="In progress"):
    """Give `login`'s PRs two snapshots each, so they MOVE."""
    for n in nums:
        for d, s in (("2026-06-05", status_a), ("2026-06-06", status_b)):
            conn.execute(
                "INSERT INTO work_item_status (taken_at, date, item_id, project, item_type, "
                "repo, number, status_raw, title) "
                "VALUES (?, ?, ?, 'o/1', 'pull_request', 'o/r', ?, ?, ?)",
                (f"{d}T00:00:00Z", d, f"{login}-{n}", n, s, f"t{n}"))
    conn.commit()


class FlowItemsTest(unittest.TestCase):
    def test_person_flow_can_return_the_counts_the_shrinkage_needs(self):
        import semantic
        import semantic_metrics as sm
        with _store() as (store, conn), patch.object(
                semantic, "stage_for", lambda _c, raw: _STAGES.get(raw, "other")):
            _work(conn, "a", peer_reviews=1)
            _board(conn, "a", range(6))
            ratios, items = sm.person_flow(conn, None, SINCE, UNTIL, with_items=True)
            self.assertEqual(items["a"], 6)
            self.assertIsInstance(ratios, dict)

    def test_the_item_map_is_not_gated_by_the_minimum(self):
        """A caller needs the count even when the ratio was withheld, to tell "too few
        items" from "no items"."""
        import semantic
        import semantic_metrics as sm
        with _store() as (store, conn), patch.object(
                semantic, "stage_for", lambda _c, raw: _STAGES.get(raw, "other")):
            _work(conn, "a", peer_reviews=1)
            _board(conn, "a", [0])
            ratios, items = sm.person_flow(conn, None, SINCE, UNTIL, with_items=True)
            self.assertEqual(items["a"], 1)
            self.assertNotIn("a", ratios, "one item is below the ratio's minimum")

    def test_an_item_that_never_moved_is_not_counted_as_smooth(self):
        """The whole point of v0.3: no movement is no reading, not a perfect score."""
        import semantic
        import semantic_metrics as sm
        with _store() as (store, conn), patch.object(
                semantic, "stage_for", lambda _c, raw: _STAGES.get(raw, "other")):
            _work(conn, "a", peer_reviews=1)
            _board(conn, "a", range(6), "Backlog", "Backlog")     # stands still
            ratios, items = sm.person_flow(conn, None, SINCE, UNTIL, with_items=True)
            self.assertNotIn("a", ratios)
            self.assertEqual(items.get("a", 0), 0)

    def test_the_old_single_value_shape_still_works(self):
        import semantic_metrics as sm
        with _store() as (store, conn):
            _work(conn, "a", peer_reviews=1)
            self.assertIsInstance(sm.person_flow(conn, None, SINCE, UNTIL), dict)


if __name__ == "__main__":
    unittest.main()
