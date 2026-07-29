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
