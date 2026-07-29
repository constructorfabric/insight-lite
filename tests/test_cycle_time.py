"""Guards for the Flow cycle-time segments.

Two of the five ("Open → first review" and "Review → merge") were permanently empty:
both were computed from pull_request.review_requested_at, which collect.py hardcodes
to None, so they came back h=None / n=0 and render dropped the cards entirely. A
reader could not tell "nothing happened in this window" from "this can never be
computed". Both halves are pinned here — the real source, and the naming of whatever
still has no data.
"""
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import render
import semantic_metrics
import store

W = ("2008-01-01T00:00:00Z", "2099-01-01T00:00:00Z")



def _src(name):
    """Read a backend module's source. Resolved from THIS file, not the working
    directory: the modules moved to backend/ and a bare Path("x.py") silently
    depended on being run from the repo root."""
    from pathlib import Path as _P
    return (_P(__file__).resolve().parents[1] / "backend" / name).read_text()


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed(tmp):
    with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
        conn = store.connect()
    t0 = datetime.now(timezone.utc) - timedelta(days=10)
    conn.execute(
        "INSERT INTO pull_request (repo, number, org, author_login, created_at, "
        "merged_at, review_requested_at, state, is_bot, is_migration, title) "
        "VALUES ('o/r', 1, 'o', 'alice', ?, ?, NULL, 'MERGED', 0, 0, 'p')",
        (_iso(t0), _iso(t0 + timedelta(hours=10))))
    # two reviews: the FIRST one is what the segment must measure
    for who, at in (("bob", t0 + timedelta(hours=4)), ("carol", t0 + timedelta(hours=6))):
        conn.execute(
            "INSERT INTO review (repo, pr_number, reviewer_login, state, submitted_at) "
            "VALUES ('o/r', 1, ?, 'APPROVED', ?)", (who, _iso(at)))
    conn.commit()
    return conn


class SourceIsTheSubmittedReviewTest(unittest.TestCase):
    def test_segments_come_from_review_submitted_at(self):
        """review_requested_at is NULL on the row; the segments must still compute."""
        with TemporaryDirectory() as tmp:
            cy = semantic_metrics.flow_report(_seed(tmp), None, *W)["cycle"]
            self.assertAlmostEqual(cy["ttfr"]["h"], 4.0, places=1)      # first review, not second
            self.assertEqual(cy["ttfr"]["n"], 1)
            self.assertAlmostEqual(cy["review_to_merge"]["h"], 6.0, places=1)
            self.assertAlmostEqual(cy["ttm"]["h"], 10.0, places=1)

    def test_the_first_review_wins_not_the_last(self):
        with TemporaryDirectory() as tmp:
            conn = _seed(tmp)
            t0 = datetime.now(timezone.utc) - timedelta(days=10)
            conn.execute(
                "INSERT INTO review (repo, pr_number, reviewer_login, state, submitted_at) "
                "VALUES ('o/r', 1, 'dave', 'APPROVED', ?)",
                (_iso(t0 + timedelta(hours=1)),))
            conn.commit()
            cy = semantic_metrics.flow_report(conn, None, *W)["cycle"]
            self.assertAlmostEqual(cy["ttfr"]["h"], 1.0, places=1)

    def test_a_pr_nobody_reviewed_contributes_nothing(self):
        with TemporaryDirectory() as tmp:
            conn = _seed(tmp)
            conn.execute("DELETE FROM review")
            conn.commit()
            cy = semantic_metrics.flow_report(conn, None, *W)["cycle"]
            self.assertIsNone(cy["ttfr"]["h"])
            self.assertEqual(cy["ttfr"]["n"], 0)
            self.assertIsNotNone(cy["ttm"]["h"])     # merge timing is unaffected

    def test_review_requested_at_is_no_longer_read(self):
        """Pin the source so a future edit cannot quietly point it back at the dead
        column: collect.py:1569 writes None there, and nothing has changed that.

        Reads _flow_item_facts, which is where the cohort load lives — slicing
        flow_report() instead would silently pass on the metric-registry snippet that
        follows it in the file, which quotes the query without executing anything."""
        src = _src("semantic_metrics.py")
        body = src[src.index("def _flow_item_facts("):]
        body = body[:body.index("\ndef ")]
        self.assertNotIn("review_requested_at\"]", body)
        self.assertIn("MIN(submitted_at)", body)


class MissingSegmentsAreNamedTest(unittest.TestCase):
    def test_a_segment_without_data_is_reported_not_dropped(self):
        with TemporaryDirectory() as tmp:
            f = semantic_metrics.flow_report(_seed(tmp), None, *W)
            env = render.flow_json({"flow": f}, {})
            keys = {c["key"] for c in env["flow"]["cycle"]}
            missing = {m["key"] for m in env["cycleMissing"]}
            self.assertIn("ttfr", keys)
            # no ready-for-review event and no issues were seeded
            self.assertIn("draft_to_ready", missing)
            self.assertTrue(missing.isdisjoint(keys), "a segment cannot be both")

    def test_missing_entries_carry_a_human_label(self):
        with TemporaryDirectory() as tmp:
            conn = _seed(tmp)
            conn.execute("DELETE FROM review")        # kills ttfr and review_to_merge
            conn.commit()
            env = render.flow_json(
                {"flow": semantic_metrics.flow_report(conn, None, *W)}, {})
        labels = {m["key"]: m["label"] for m in env["cycleMissing"]}
        self.assertEqual(labels["ttfr"], "Open → first review")
        self.assertEqual(labels["review_to_merge"], "Review → merge")

    def test_no_cohort_reports_nothing_missing_on_purpose(self):
        """With no cohort the whole view degrades to one hint and there is no
        cycle section to annotate, so naming segments there would be noise with
        no consumer. Absence here is the deliberate answer, not an oversight."""
        env = render.flow_json({"flow": {"has_data": False}}, {})
        self.assertEqual(env["flow"], {"hasData": False})
        self.assertNotIn("cycleMissing", env)


def _seed_cycle(tmp, rows, name="c.db"):
    """A cohort of PRs described as (repo, ttfr_hours, r2m_hours, merged, reviewed).
    `ttfr` places the first submitted review, `r2m` the merge after it — so a row's
    total lead time is exactly ttfr + r2m, which is the property the whole-cycle bar
    is built on."""
    with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / name)}):
        conn = store.connect()
    t0 = datetime.now(timezone.utc) - timedelta(days=20)
    for i, (repo, ttfr, r2m, merged, reviewed) in enumerate(rows, start=1):
        conn.execute(
            "INSERT INTO pull_request (repo, number, org, author_login, created_at, "
            "merged_at, review_requested_at, state, is_bot, is_migration, title) "
            "VALUES (?, ?, 'o', 'alice', ?, ?, NULL, ?, 0, 0, 'p')",
            (repo, i, _iso(t0),
             _iso(t0 + timedelta(hours=ttfr + r2m)) if merged else None,
             "MERGED" if merged else "OPEN"))
        if reviewed:
            conn.execute(
                "INSERT INTO review (repo, pr_number, reviewer_login, state, submitted_at) "
                "VALUES (?, ?, 'bob', 'APPROVED', ?)",
                (repo, i, _iso(t0 + timedelta(hours=ttfr))))
    conn.commit()
    return conn


class WholeCycleBarTest(unittest.TestCase):
    """The whole cycle as one length with its legs inside it — added because five
    medians measured over five different populations cannot be added into a total,
    which is what a reader looking at "cycle time" expects to see."""

    # totals are 2, 22, 4, 32 -> chosen so the sum of the leg medians and the median
    # total genuinely DISAGREE; most tidy fixtures make them coincide and would let a
    # bug that conflates the two pass
    ROWS = [("o/a", 1, 1, True, True), ("o/a", 2, 20, True, True),
            ("o/a", 3, 1, True, True), ("o/a", 30, 2, True, True)]

    def test_legs_and_both_totals_are_reported_separately(self):
        with TemporaryDirectory() as tmp:
            bar = semantic_metrics.flow_report(_seed_cycle(tmp, self.ROWS), None, *W)["cycle_bar"]
        self.assertTrue(bar["has_data"])
        self.assertEqual(bar["n"], 4)
        legs = {l["key"]: l["h"] for l in bar["legs"]}
        self.assertAlmostEqual(legs["ttfr"], 2.5, places=1)             # median of 1,2,3,30
        self.assertAlmostEqual(legs["review_to_merge"], 1.5, places=1)  # median of 1,20,1,2
        self.assertAlmostEqual(bar["legs_sum_h"], 4.0, places=1)        # the bar's width
        self.assertAlmostEqual(bar["median_total_h"], 13.0, places=1)   # median of 2,22,4,32
        self.assertNotAlmostEqual(bar["legs_sum_h"], bar["median_total_h"], places=1,
                                  msg="the fixture must exercise the divergence")

    def test_the_tail_is_reported_not_just_the_middle(self):
        with TemporaryDirectory() as tmp:
            bar = semantic_metrics.flow_report(_seed_cycle(tmp, self.ROWS), None, *W)["cycle_bar"]
        self.assertAlmostEqual(bar["p75_total_h"], 22.0, places=1)
        self.assertAlmostEqual(bar["p90_total_h"], 32.0, places=1)

    def test_leg_shares_are_of_the_bar_and_add_to_100(self):
        with TemporaryDirectory() as tmp:
            bar = semantic_metrics.flow_report(_seed_cycle(tmp, self.ROWS), None, *W)["cycle_bar"]
        self.assertAlmostEqual(sum(l["pct"] for l in bar["legs"]), 100.0, places=1)

    def test_cohort_is_only_prs_that_were_both_reviewed_and_merged(self):
        """The point of the panel is that the legs add up per PR, which they cannot do
        for a PR that was never reviewed or never merged. Those are excluded here even
        though they still count towards the individual median cards above."""
        rows = self.ROWS + [("o/a", 5, 5, True, False),    # merged, nobody reviewed
                            ("o/a", 5, 5, False, True)]    # reviewed, still open
        with TemporaryDirectory() as tmp:
            f = semantic_metrics.flow_report(_seed_cycle(tmp, rows), None, *W)
        self.assertEqual(f["cycle_bar"]["n"], 4)
        self.assertEqual(f["cycle"]["ttm"]["n"], 5, "the ttm card still sees the unreviewed merge")
        self.assertEqual(f["cycle"]["ttfr"]["n"], 5, "the ttfr card still sees the open PR")

    def test_no_completed_pr_degrades_instead_of_inventing_a_bar(self):
        with TemporaryDirectory() as tmp:
            f = semantic_metrics.flow_report(
                _seed_cycle(tmp, [("o/a", 5, 5, False, True)]), None, *W)
        self.assertEqual(f["cycle_bar"], {"has_data": False, "n": 0})
        self.assertIsNone(render.flow_json({"flow": f}, {})["flow"]["cycleBar"])

    def test_per_repo_split_needs_enough_prs_and_counts_what_it_dropped(self):
        rows = self.ROWS + [("o/b", 1, 1, True, True), ("o/b", 2, 2, True, True)]
        with TemporaryDirectory() as tmp:
            bar = semantic_metrics.flow_report(_seed_cycle(tmp, rows), None, *W)["cycle_bar"]
        self.assertEqual([r["repo"] for r in bar["by_repo"]], ["o/a"])  # o/b has only 2
        self.assertEqual(bar["repos_total"], 2, "the dropped repo is still counted")
        self.assertEqual(bar["repo_min"], 3)

    def test_per_repo_bars_share_one_scale(self):
        """Widths are measured against the slowest row, so a fast repo and a slow one
        cannot draw the same bar (each normalised to itself would do exactly that)."""
        rows = ([("o/a", 1, 1, True, True)] * 3) + ([("o/b", 10, 10, True, True)] * 3)
        with TemporaryDirectory() as tmp:
            env = render.flow_json(
                {"flow": semantic_metrics.flow_report(_seed_cycle(tmp, rows), None, *W)}, {})
        rows_out = {r["repo"]: r for r in env["flow"]["cycleBar"]["byRepo"]}
        fast = rows_out["o/a"]["ttfrPct"] + rows_out["o/a"]["r2mPct"]
        slow = rows_out["o/b"]["ttfrPct"] + rows_out["o/b"]["r2mPct"]
        self.assertAlmostEqual(slow, 100.0, places=1)
        self.assertLess(fast, slow / 5)


class TilesCarryATrendTest(unittest.TestCase):
    """"Flow health — I'd want to see a trend" / "at least whether it got better or
    worse than last period". A sparkline that disagreed with the tile above it would be
    worse than none, so both come from the same per-item facts."""

    def test_every_bucket_matches_the_headline_when_nothing_changes(self):
        """Eight PRs, one per day, all with identical timings: every sub-window must
        report the same median as the whole window, so the line is flat by arithmetic
        rather than by luck."""
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "s.db")}):
                conn = store.connect()
            t0 = datetime.now(timezone.utc) - timedelta(days=20)
            for i in range(8):
                created = t0 + timedelta(days=i)
                conn.execute(
                    "INSERT INTO pull_request (repo, number, org, author_login, created_at, "
                    "merged_at, review_requested_at, state, is_bot, is_migration, title) "
                    "VALUES ('o/r', ?, 'o', 'alice', ?, ?, NULL, 'MERGED', 0, 0, 'p')",
                    (i + 1, _iso(created), _iso(created + timedelta(hours=5))))
                conn.execute(
                    "INSERT INTO review (repo, pr_number, reviewer_login, state, submitted_at) "
                    "VALUES ('o/r', ?, 'bob', 'APPROVED', ?)",
                    (i + 1, _iso(created + timedelta(hours=2))))
            conn.commit()
            since = _iso(t0)
            until = _iso(t0 + timedelta(days=8))
            items = semantic_metrics._flow_item_facts(conn, None, since, until)
        self.assertEqual(len(items), 8)
        self.assertAlmostEqual(semantic_metrics._flow_scalars(items)["cycle_ttfr"], 2.0, places=1)
        pts = semantic_metrics.flow_spark(items, since, until)["cycle_ttfr_pts"]
        ys = {p.split(",")[1] for p in pts.split()}
        # not exactly 8: the bucket arithmetic is store._bucketize's, which divides the
        # span into n+1 to keep the last day inside the last bucket, so a one-item-per-
        # day fixture doubles up in the first slice and leaves the last one empty
        self.assertGreaterEqual(len(pts.split()), 6)
        self.assertEqual(len(ys), 1, f"a constant metric must draw a flat line, got {pts}")

    def test_the_window_is_clamped_to_the_data_not_to_the_year_2008(self):
        """All-time is the default period and its nominal start is 2008. Without the
        clamp every sparkline on the page would be seven empty buckets and a spike."""
        with TemporaryDirectory() as tmp:
            conn = _seed_cycle(tmp, [("o/a", 1, 1, True, True)] * 4, name="k.db")
            items = semantic_metrics._flow_item_facts(conn, None, *W)
        pts = semantic_metrics.flow_spark(items, *W)
        # every item is inside a few hours of the same day, so the clamp collapses the
        # window to a single bucket and there is nothing to draw — the honest answer
        self.assertTrue(all(v == "" for v in pts.values()), pts)

    def test_all_flow_keys_get_a_delta_including_the_rewind_count(self):
        self.assertIn("rewinds_qa_to_dev", semantic_metrics.FLOW_DELTA_KEYS)
        self.assertNotIn("rewinds_qa_to_dev", semantic_metrics.FLOW_KPI_KEYS,
                         "no sparkline for it — the snapshot history is too thin")
        with TemporaryDirectory() as tmp:
            conn = _seed_cycle(tmp, [("o/a", 1, 1, True, True)] * 4, name="d.db")
            kpis = semantic_metrics.flow_kpis(conn, None, *W)
        for key in semantic_metrics.FLOW_DELTA_KEYS:
            self.assertIn(key, kpis, f"{key} has no value to diff against")

    def test_chips_and_sparklines_reach_the_json(self):
        with TemporaryDirectory() as tmp:
            conn = _seed_cycle(tmp, WholeCycleBarTest.ROWS, name="j.db")
            # one explicit "changes requested" so the rate under test is not 0 in both
            # windows — a metric that is zero on both sides gets no chip at all, which
            # is _delta_chip's own rule and would make this test vacuous
            conn.execute("INSERT INTO review (repo, pr_number, reviewer_login, state, "
                         "submitted_at) VALUES ('o/a', 1, 'bob', 'CHANGES_REQUESTED', ?)",
                         (_iso(datetime.now(timezone.utc) - timedelta(days=19)),))
            conn.commit()
            f = semantic_metrics.flow_report(conn, None, *W)
            f["deltas"] = render.delta_map(
                f, semantic_metrics.flow_kpis(conn, None, *W),
                keys=semantic_metrics.FLOW_DELTA_KEYS)
        env = render.flow_json({"flow": f}, {})["flow"]
        self.assertEqual(set(env["healthTrend"]),
                         {"crRate", "reopenRate", "bounceRate", "rereqRate"})
        for t in env["healthTrend"].values():
            self.assertIn("sparkPts", t)
            self.assertIn("delta", t)
        self.assertEqual(env["health"]["crRate"], 25.0)
        # diffed against itself, so a non-zero metric must read as unchanged
        self.assertEqual(env["healthTrend"]["crRate"]["delta"]["cls"], "flat")
        self.assertEqual([c["delta"]["cls"] for c in env["cycle"]],
                         ["flat"] * len(env["cycle"]))
        # present as a key even when the count is 0 on both sides and there is no chip
        self.assertIn("delta", env["rewinds"])


if __name__ == "__main__":
    unittest.main()
