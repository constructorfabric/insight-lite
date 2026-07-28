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
        column: collect.py:1569 writes None there, and nothing has changed that."""
        src = _src("semantic_metrics.py")
        body = src[src.index("def flow_report("):]
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


if __name__ == "__main__":
    unittest.main()
