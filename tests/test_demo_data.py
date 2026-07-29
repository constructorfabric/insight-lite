"""The demo dataset must stay a working stand-in for a real collection.

This is the fixture the suite did not have. Everything else seeds the granular tables,
which is enough to test queries but not the render: the report is built from a run blob
that only collect.py used to produce, so `build_model` and the whole template layer went
untested end to end. demo.build() now produces that blob, and these tests are what keep
it honest — if the renderer starts needing a field the generator does not supply, they
fail here rather than the demo quietly rendering an emptier report than it should.

That property is the reason the dataset is GENERATED rather than a committed .db file:
a checked-in binary would keep loading after the contract moved, and would keep looking
fine while meaning less. Generation costs ~0.07s, so there is nothing to buy by freezing
it.
"""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import demo


class BlobShapeTest(unittest.TestCase):
    """demo.build() is pure, so its output can be checked without touching a DB."""

    ANCHOR = "2026-06-30"

    def test_it_is_deterministic_for_a_fixed_anchor(self):
        """Tests pass an anchor precisely so the dataset is reproducible; the CLI omits
        it so a fresh demo ends today and the 7-day window has data in it."""
        self.assertEqual(demo.build(self.ANCHOR), demo.build(self.ANCHOR))

    def test_the_anchor_moves_the_window(self):
        a = demo.build("2026-06-30")
        b = demo.build("2026-03-31")
        self.assertNotEqual(a["generated_at"], b["generated_at"])
        self.assertNotEqual(a["window_start"], b["window_start"])

    def test_every_key_collect_writes_is_present(self):
        """The blob is a contract with render.build_model. Missing keys do not crash —
        most are read with .get() — they silently empty a panel, which is the failure
        this whole test module exists to prevent."""
        blob = demo.build(self.ANCHOR)
        for key in ("generated_at", "org", "orgs", "lookback_days", "all_time",
                    "window_start", "window_labels", "window_since", "members",
                    "repos", "people", "forkers", "weekly", "elements", "reviews",
                    "reviews_company_ttm", "bots", "identity", "api", "pct",
                    "studio_provenance", "gears_usage", "fabric_trackers",
                    "ai_precision", "scope_targets"):
            self.assertIn(key, blob, f"blob is missing {key}")

    def test_people_carry_every_column_the_store_normalises(self):
        """store.upsert_run writes person_runs from these names; a missing one becomes
        a NULL column rather than an error."""
        import store
        blob = demo.build(self.ANCHOR)
        for login, p in blob["people"].items():
            for col in store.PERSON_COLS:
                self.assertIn(col, p, f"{login} is missing {col}")

    def test_repos_carry_every_column_the_store_normalises(self):
        import store
        blob = demo.build(self.ANCHOR)
        for key, r in blob["repos"].items():
            for col in store.REPO_COLS:
                self.assertIn(col, r, f"{key} is missing {col}")

    def test_the_data_is_not_flat(self):
        """A dataset where everyone has identical numbers makes rankings, medians and
        the "top N" panels meaningless — and would hide a sorting bug."""
        blob = demo.build(self.ANCHOR)
        commits = [p["commits"] for p in blob["people"].values()]
        self.assertGreater(max(commits), min(commits) * 3)
        self.assertGreater(len({p["company"] for p in blob["people"].values()}), 2)

    def test_nobody_in_it_could_be_mistaken_for_a_real_person(self):
        """The Alice/Bob convention is load-bearing: these names end up in published
        screenshots and in test output."""
        blob = demo.build(self.ANCHOR)
        for login, p in blob["people"].items():
            self.assertTrue(p["emails"][0].endswith("@example.com"),
                            f"{login} has a non-example address")


class GranularTablesTest(unittest.TestCase):
    """The granular tables are a SECOND view of the dataset, and they need their own
    assertions.

    BlobShapeTest above checks the run blob, which feeds the run-based report. Every
    React view and every MCP tool reads `commits` / `pull_request` / `issue` instead —
    and for a while those told a completely different story: the seeding gate was
    `if (n + pi) % 3 == 0: continue`, which with 12 people collapsed to `pi % 3`, so
    four of them (alice, dave, grace, judy) had ZERO rows and the other eight had
    byte-identical totals. The blob's 12x spread was intact the whole time, so
    test_the_data_is_not_flat passed and the People view still showed 8 identical
    people. Checking the blob is not checking the data.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        d = Path(self._tmp.name)
        (d / "history").mkdir()
        self._env = patch.dict(os.environ, {
            "DATA_DIR": str(d), "REPORT_DB": str(d / "history" / "report.db")})
        self._env.start()
        demo.seed(anchor="2026-06-30")
        self.blob = demo.build("2026-06-30")

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def _by_author(self, table, col="author_login"):
        import store
        conn = store.connect()
        try:
            rows = conn.execute(
                f"SELECT {col} a, COUNT(*) n FROM {table} GROUP BY a").fetchall()
        finally:
            conn.close()
        return {r[0]: r[1] for r in rows}

    def test_every_person_has_commits(self):
        counts = self._by_author("commits")
        missing = sorted(set(self.blob["people"]) - set(counts))
        self.assertEqual(missing, [],
                         f"these people are in the blob but have no commit rows, so "
                         f"they are invisible to every React view: {missing}")

    def test_every_person_has_pull_requests(self):
        counts = self._by_author("pull_request")
        missing = sorted(set(self.blob["people"]) - set(counts))
        self.assertEqual(missing, [], f"no pull requests for {missing}")

    def test_commit_volume_is_not_flat(self):
        """Identical totals make every ranking, median and top-N panel meaningless —
        and would hide a sorting bug behind plausible-looking output."""
        counts = self._by_author("commits")
        self.assertGreater(len(set(counts.values())), 3,
                           f"commit totals are near-identical: {sorted(counts.values())}")
        self.assertGreater(max(counts.values()), min(counts.values()) * 3,
                           f"no meaningful spread: {sorted(counts.values())}")

    def test_the_tables_agree_with_the_blob_about_who_is_busiest(self):
        """Shape, not equality: the two are different windows and different metrics, but
        they describe one fictional company and must not contradict each other."""
        counts = self._by_author("commits")
        blob_commits = {lg: p["commits"] for lg, p in self.blob["people"].items()}
        busiest = max(blob_commits, key=lambda k: blob_commits[k])
        quietest = min(blob_commits, key=lambda k: blob_commits[k])
        self.assertGreater(counts[busiest], counts[quietest],
                           f"{busiest} leads the blob but not the commit table")


class CycleTimeShapeTest(unittest.TestCase):
    """The demo PR timings must stay ANTI-CORRELATED across the two cycle legs.

    semantic_metrics.flow_cycle_bar() draws the whole PR cycle as one length with
    open→first-review and first-review→merge inside it. Its first version used
    median(ttfr) + median(r2m) as the bar's width, and this fixture is the reason that
    survived review: every demo PR opened at 09:00, was reviewed at 12:00 and merged at
    15:00, so the sum of the leg medians equalled the median total to the decimal (8.5h
    against 8.5h). On production the same code drew a 4.6h bar directly under a line
    reading 17.5h, because real PRs are slow in one leg or the other and rarely both.

    tests/test_cycle_time.py::WholeCycleBarTest pins the metric against a hand-built
    anti-correlated fixture. This pins the complementary half — that the SEEDER produces
    that shape — so the demo dataset cannot quietly drift back to a form in which a
    "median of the parts equals the median of the whole" bug looks correct.
    """

    ANCHOR = "2026-06-30"

    def setUp(self):
        self._tmp = TemporaryDirectory()
        d = Path(self._tmp.name)
        (d / "history").mkdir()
        self._env = patch.dict(os.environ, {
            "DATA_DIR": str(d), "REPORT_DB": str(d / "history" / "report.db")})
        self._env.start()
        demo.seed(anchor=self.ANCHOR)
        blob = demo.build(self.ANCHOR)
        self._window = (blob["window_start"] + "T00:00:00Z", blob["generated_at"])

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def _bar(self):
        import semantic_metrics
        import store
        conn = store.connect()
        try:
            return semantic_metrics.flow_report(conn, None, *self._window)["cycle_bar"]
        finally:
            conn.close()

    def _cohort(self):
        """The same cohort flow_cycle_bar measures: merged PRs that also got a review."""
        import semantic_metrics
        import store
        conn = store.connect()
        try:
            items = semantic_metrics._flow_item_facts(conn, None, *self._window)
        finally:
            conn.close()
        return [r for r in items
                if r["is_pr"] and r["ttfr"] is not None and r["r2m"] is not None
                and r["ttm"] is not None and r["ttm"] > 0]

    def test_the_sum_of_the_leg_medians_is_far_below_the_median_total(self):
        """The property the old fixture did not have. A ratio, not fixed hours, so
        retuning the mix is allowed and flattening it is not."""
        bar = self._bar()
        self.assertTrue(bar["has_data"])
        self.assertGreater(bar["n"], 100, "too small a cohort to mean anything")
        legs = sum(l["h"] for l in bar["legs"])
        self.assertLess(
            legs, bar["median_total_h"] / 2,
            f"the leg medians sum to {legs}h against a median total of "
            f"{bar['median_total_h']}h — the demo PR timings are back to a shape where "
            f"adding the leg medians passes for the total")

    def test_the_legs_still_add_up_per_pull_request(self):
        """The identity the panel is built on: it must hold exactly for every PR, since
        it is a property of the timestamps rather than of the arithmetic."""
        cohort = self._cohort()
        self.assertTrue(cohort)
        for r in cohort:
            self.assertAlmostEqual(r["ttfr"] + r["r2m"], r["ttm"], places=6,
                                   msg=f"{r['key']} legs do not sum to its total")

    def test_both_behaviours_are_present_in_quantity(self):
        """Anti-correlation, stated directly: a meaningful share of PRs waited for a
        first look and then merged fast, and a meaningful share were looked at at once
        and then sat in review. Making every PR slow in both legs would also break the
        median-of-parts bug, and would be a different, less honest dataset."""
        cohort = self._cohort()
        slow = 6.0                       # hours; anything above this is "waited"
        camps = {
            "waited, then merged fast": [r for r in cohort
                                         if r["ttfr"] > slow and r["r2m"] <= slow],
            "looked at at once, then lingered": [r for r in cohort
                                                 if r["ttfr"] <= slow and r["r2m"] > slow],
        }
        for name, rows in camps.items():
            self.assertGreater(len(rows), len(cohort) * 0.15,
                               f"only {len(rows)} of {len(cohort)} PRs are “{name}”")

    def test_the_totals_have_a_tail_not_just_a_middle(self):
        """p75/p90 barely above the median (they were 10.0h and 11.0h against 8.5h) hide
        the slow reviews a reader remembers, and leave the tail figures on the panel
        untested against anything but a rounding difference."""
        bar = self._bar()
        self.assertGreater(bar["p75_total_h"], bar["median_total_h"] * 1.3,
                           f"p75 {bar['p75_total_h']}h is not meaningfully above the "
                           f"median {bar['median_total_h']}h")
        self.assertGreater(bar["p90_total_h"], bar["p75_total_h"] * 1.5,
                           f"p90 {bar['p90_total_h']}h is not meaningfully above p75 "
                           f"{bar['p75_total_h']}h")

    def test_nothing_claims_a_merge_that_has_not_happened(self):
        """Giving PRs a real lead time made some of them run past the end of the data.
        Left as closed-with-a-future-merge_at, they made the report disagree with itself:
        panels that count merges without a date filter saw them, the ones that window by
        merged_at did not. Those PRs are seeded OPEN instead, so both counts match."""
        import store
        conn = store.connect()
        try:
            cutoff = self._window[1]
            future = conn.execute(
                "SELECT COUNT(*) FROM pull_request WHERE merged_at IS NOT NULL"
                " AND merged_at<>'' AND merged_at>?", (cutoff,)).fetchone()[0]
            unfiltered = conn.execute(
                "SELECT COUNT(*) FROM pull_request"
                " WHERE merged_at IS NOT NULL AND merged_at<>''").fetchone()[0]
            windowed = conn.execute(
                "SELECT COUNT(*) FROM pull_request WHERE merged_at IS NOT NULL"
                " AND merged_at<>'' AND merged_at>=? AND merged_at<=?",
                self._window).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(future, 0, f"{future} PRs merge after the data ends")
        self.assertEqual(unfiltered, windowed,
                         "the merged count depends on whether you window by merged_at")

    def test_there_is_open_work_and_in_flight_can_see_it(self):
        """Every demo PR used to open and merge on the same day, so the Flow page's
        leading section — "What is open right now" — rendered "No open pull requests in
        this scope" in every screenshot of it.

        Also pins the state CASING, which is the trap here: collect.py stores the GraphQL
        enum verbatim (OPEN/CLOSED/MERGED) and store.in_flight() compares state='OPEN'
        without UPPER(), so a fixture writing lowercase 'open' seeds open PRs that the
        panel then reports as none. Most other queries wrap the column in UPPER() and
        would have hidden the mistake."""
        import store
        conn = store.connect()
        try:
            states = dict(conn.execute(
                "SELECT state, COUNT(*) FROM pull_request GROUP BY state").fetchall())
            inf = store.in_flight(conn)
        finally:
            conn.close()
        self.assertEqual(set(states) - {"OPEN", "MERGED"}, set(),
                         f"unexpected PR states {sorted(states)} — the collector writes "
                         f"the uppercase GraphQL enum")
        self.assertGreater(states.get("OPEN", 0), 0, "no open PRs in the demo data")
        self.assertEqual(inf["n"], states["OPEN"],
                         "store.in_flight cannot see the open PRs the seeder wrote")
        self.assertGreater(len(inf["people"]), 1, "open work sits with a single person")
        self.assertIsNotNone(inf["oldest_age_d"])


class RendersEndToEndTest(unittest.TestCase):
    """The point of the whole exercise: a seeded store renders the actual report."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        d = Path(self._tmp.name)
        (d / "history").mkdir()
        self._env = patch.dict(os.environ, {
            "DATA_DIR": str(d), "REPORT_DB": str(d / "history" / "report.db")})
        self._env.start()
        self.counts = demo.seed(anchor="2026-06-30")

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_seed_writes_all_the_tables(self):
        for table in ("person", "repo", "commits", "pull_request", "snapshots",
                      "traffic", "runs"):
            self.assertGreater(self.counts[table], 0, f"{table} is empty")

    def test_build_model_succeeds(self):
        import render
        model = render.build_model(render.load_data())
        self.assertTrue(model.get("table"), "the per-person table is empty")
        self.assertTrue(model.get("all_block"), "no all-time block")

    def test_the_full_report_renders(self):
        """Exercises build_model plus every template. Nothing else in the suite does."""
        import render
        html = render.render_report(render.build_model(render.load_data()))
        self.assertGreater(len(html), 50_000, "suspiciously small report")
        for marker in ("Alice Anderson", "platform-core", "Northwind Systems"):
            self.assertIn(marker, html, f"{marker} did not reach the report")

    def test_the_panels_that_need_the_blob_are_populated(self):
        """A blob with the right keys but empty values would render a report that looks
        fine and says nothing — so assert the panels actually have rows."""
        import render
        model = render.build_model(render.load_data())
        self.assertTrue(model.get("element_rows"), "no element rollup")
        self.assertTrue(model.get("reviews_by_company"), "no review breakdown")
        self.assertTrue(model.get("non_contributors"),
                        "the fork-without-contributing panel is empty")
        self.assertTrue(model.get("weekly", {}).get("weeks"), "no weekly activity")


if __name__ == "__main__":
    unittest.main()
