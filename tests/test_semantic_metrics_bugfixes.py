"""Regression tests for two semantic_metrics defects with no natural home elsewhere:

  * the `capped` flag on the three paginated Python-side drills was computed as
    `total > len(shown)`, which is wrong once `offset > 0` — it claimed more pages
    existed when the reader was already on the last one. It must be `total > offset+limit`,
    the form drill_person_flow already used.
  * delivery_spark's bucket edges are day-floors (…T00:00:00Z). The last edge landed on
    day(until)T00:00:00Z while callers pass `until` as …T23:59:59Z, so items created on
    the final day fell into no bucket and vanished from every sparkline's last point.
"""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import semantic
import semantic_metrics as sm
import store


def _db():
    tmp = TemporaryDirectory()
    with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp.name) / "t.db")}):
        conn = store.connect()
    return tmp, conn


class CappedWithOffsetTest(unittest.TestCase):
    """capped must mean "there are rows beyond this page", which depends on the offset."""

    def setUp(self):
        self._tmp, self.conn = _db()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.conn.close)

    def test_drill_flow_stage_last_page_is_not_capped(self):
        # three items, all currently in the same stage
        for n in (1, 2, 3):
            self.conn.execute(
                "INSERT INTO work_item_status (taken_at, date, item_id, project, item_type, "
                "repo, number, status_raw, title) VALUES (?,?,?,?,?,?,?,?,?)",
                ("2026-06-01T00:00:00Z", "2026-06-01", f"IT{n}", "P", "Issue",
                 "o/r", n, "In Progress", f"t{n}"))
        self.conn.commit()
        with patch.object(semantic, "stage_for", lambda _c, raw: "in_progress"):
            page1 = sm.drill_flow_stage(self.conn, "in_progress", None, limit=2, offset=0)
            page2 = sm.drill_flow_stage(self.conn, "in_progress", None, limit=2, offset=2)
        self.assertEqual(page1["total"], 3)
        self.assertTrue(page1["capped"], "3 > 0 + 2 — a second page exists")
        self.assertEqual(page2["shown"], 1)
        self.assertFalse(page2["capped"], "3 > 2 + 2 is False — this is the last page")

    def test_drill_issue_category_last_page_is_not_capped(self):
        for n in (1, 2, 3):
            self.conn.execute(
                "INSERT INTO issue (repo, number, is_bot, is_migration, created_at, title) "
                "VALUES (?,?,0,0,?,?)", ("o/r", n, "2026-06-01T00:00:00Z", f"t{n}"))
        self.conn.commit()
        w = ("2026-05-01T00:00:00Z", "2026-07-01T00:00:00Z")
        with patch.object(semantic, "categorize_issue", lambda *a: "bug"):
            page2 = sm.drill_issue_category(self.conn, *w, "bug", limit=2, offset=2)
        self.assertEqual(page2["total"], 3)
        self.assertEqual(page2["shown"], 1)
        self.assertFalse(page2["capped"], "last page, no more rows beyond it")

    def test_drill_ci_runs_last_page_is_not_capped(self):
        for n in (1, 2, 3):
            self.conn.execute(
                "INSERT INTO ci_run (repo, run_id, workflow, event, conclusion, duration_s, "
                "created_at) VALUES (?,?,?,?,?,?,?)",
                ("o/r", n, "ci", "pull_request", "success", 60, "2026-06-01T00:00:00Z"))
        self.conn.commit()
        w = ("2026-05-01T00:00:00Z", "2026-07-01T00:00:00Z")
        with patch.object(semantic, "ci_role", lambda *a: "gate"):
            page2 = sm.drill_ci_runs(self.conn, *w, limit=2, offset=2)
        self.assertEqual(page2["total"], 3)
        self.assertEqual(page2["shown"], 1)
        self.assertFalse(page2["capped"], "last page, no more rows beyond it")


class DeliverySparkLastBucketTest(unittest.TestCase):
    """An item created on the final day of the window must be counted by delivery_spark."""

    def setUp(self):
        self._tmp, self.conn = _db()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.conn.close)

    def test_final_day_pr_lands_in_the_last_bucket(self):
        # One PR, created late on the window's final day. `until` is the T23:59:59Z form
        # callers pass. Under the old day-floor last edge (…-10T00:00:00Z) this PR was
        # > the edge and fell in no bucket, so prs_total was all zeros -> empty polyline.
        # Reaching the true `until` puts it in the last bucket.
        self.conn.execute(
            "INSERT INTO pull_request (repo, number, author_login, created_at, merged_at, "
            "state, is_bot, is_migration) VALUES "
            "('o/r', 1, 'alice', '2026-06-10T23:00:00Z', '2026-06-10T23:30:00Z', 'MERGED', 0, 0)")
        self.conn.commit()
        # sanity: the full window sees the PR
        self.assertEqual(
            sm.delivery_metrics(self.conn, "2026-06-01T00:00:00Z",
                                "2026-06-10T23:59:59Z")["prs_total"], 1)
        sp = sm.delivery_spark(self.conn, "2026-06-01T00:00:00Z", "2026-06-10T23:59:59Z")
        self.assertTrue(sp.get("prs_total_pts"),
                        "the final-day PR reaches the last bucket, so the trend is drawn")


if __name__ == "__main__":
    unittest.main()
