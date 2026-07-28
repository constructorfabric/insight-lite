"""Tests for the /delivery React route (see
docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P3):

- render.delivery_json(): a pure function fed a hand-built `pr` dict shaped
  like serve_delivery's own `{"delivery": semantic_metrics.window_block(...)}`
  (+ an optional 'deltas' dict) — same convention as tests/test_trend_api.py's
  TrendJsonTest / tests/test_overview_api.py's OverviewJsonTest.
- GET /api/report/delivery: the same ThreadingHTTPServer + isolated REPORT_DB
  harness as OverviewApiEndpointTest/TrendApiEndpointTest — validation paths
  that don't need real collected data.
"""
import json
import os
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import render


def _delivery(**over):
    d = {
        "issues_total": 20, "issues_by_category": {"bug": 5, "feature": 15},
        "issues_closed": 12, "issue_close_rate": 60.0, "defect_rate": 25.0,
        "issue_median_time_to_close_days": 2.5,
        "prs_total": 10, "pr_merge_rate": 80.0, "pr_abandon_rate": 10.0,
        "pr_median_additions": 120, "pr_median_changed_files": 4,
        "pr_reviewed_rate": 90.0, "pr_time_to_first_review_h": 3.5,
        "pr_reverts": 1,
        "ci_gate_runs": 40, "ci_pass_rate": 95.0, "ci_median_duration_s": 185,
        "flow_stages": [
            {"key": "backlog", "name": "Backlog", "color": "#9aa3b2", "count": 3},
            {"key": "in_progress", "name": "In progress", "color": "#8b5cf6", "count": 5},
            {"key": "done", "name": "Done", "color": "#10b981", "count": 2},
        ],
        "flow_total": 10, "flow_unmapped": 0,
        "spark": {},
    }
    d.update(over)
    return d


def _meta(**over):
    meta = {
        "org": "acme-org", "all_time": True, "window_start": "2008-01-01", "lookback_days": 100,
        "generated": "2026-07-22T16:10:05Z", "all_label": "All-time",
        "scope_targets": {"org": ["acme-org"]}, "window_labels": ["30d", "all"],
    }
    meta.update(over)
    return meta


class DeliveryJsonTest(unittest.TestCase):
    def test_envelope_shape_matches_overview_trend_convention(self):
        out = render.delivery_json({"delivery": _delivery()}, _meta())
        self.assertTrue(out["ok"])
        self.assertEqual(out["meta"]["org"], "acme-org")
        self.assertEqual(out["meta"]["generatedText"], "2026-07-22 16:10")
        self.assertEqual(out["period"], {"preset": "all", "label": "All-time", "from": None, "to": None})
        self.assertEqual(out["periodPresets"], [{"key": "30d", "label": "30 days"},
                                                 {"key": "all", "label": "All-time"}])
        self.assertEqual(out["scopeTargets"], {"org": ["acme-org"]})

    def test_degrades_gracefully_on_empty_input(self):
        out = render.delivery_json({}, _meta())
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["kpis"]), 4)
        self.assertEqual(len(out["ci"]), 2)
        self.assertEqual(len(out["pr"]), 7)
        self.assertEqual(out["mix"]["rows"], [])
        self.assertFalse(out["flow"]["hasData"])
        # every tile still has a formatted "no data" value, never raises
        self.assertEqual(out["kpis"][0]["value"], "0")
        self.assertEqual(out["kpis"][1]["value"], "—")

    def test_kpi_tiles_four_in_order_with_formatted_values(self):
        out = render.delivery_json({"delivery": _delivery()}, _meta())
        kpis = out["kpis"]
        self.assertEqual([t["label"] for t in kpis],
                          ["issues opened", "close rate", "defect rate", "median time-to-close"])
        self.assertEqual(kpis[0]["value"], "20")
        self.assertEqual(kpis[0]["drill"], {"drill": "issue"})
        self.assertEqual(kpis[1]["value"], "60%")
        self.assertEqual(kpis[1]["sub"], "12 closed")
        self.assertIsNone(kpis[1]["drill"])
        self.assertEqual(kpis[2]["value"], "25%")
        self.assertEqual(kpis[2]["drill"], {"drill": "issue", "category": "bug"})
        self.assertEqual(kpis[3]["value"], "2.5d")

    def test_ci_tiles(self):
        out = render.delivery_json({"delivery": _delivery()}, _meta())
        ci = out["ci"]
        self.assertEqual([t["label"] for t in ci], ["CI pass rate", "CI median duration"])
        self.assertEqual(ci[0]["value"], "95%")
        self.assertEqual(ci[0]["sub"], "40 gate runs")
        self.assertEqual(ci[0]["drill"], {"drill": "ci"})
        self.assertEqual(ci[1]["value"], "3m05s")

    def test_pr_tiles_seven_in_order(self):
        out = render.delivery_json({"delivery": _delivery()}, _meta())
        pr = out["pr"]
        self.assertEqual([t["label"] for t in pr],
                          ["PRs opened", "merge rate", "abandoned", "median PR size",
                           "reviewed", "time to first review", "reverts"])
        self.assertEqual(pr[0]["value"], "10")
        self.assertEqual(pr[3]["value"], "120")            # _loc(120) < 1000 -> raw
        self.assertEqual(pr[3]["sub"], "4 files")
        self.assertEqual(pr[5]["value"], "3.5h")
        self.assertEqual(pr[5]["drill"], {"tip": "from review requested (else opened) to the first "
                                                  "review submitted; median over reviewed PRs"})

    def test_lower_better_flips_delta_colour_not_arrow(self):
        deltas = {"defect_rate": {"diff": 5, "prev": 20, "pct": 25, "dir": "up"}}
        out = render.delivery_json({"delivery": _delivery(deltas=deltas)}, _meta())
        chip = out["kpis"][2]["delta"]
        self.assertEqual(chip["text"], "▲ 25%")
        self.assertEqual(chip["cls"], "down")     # up is bad for defect rate -> colour flips to down

    def test_mix_rows_share_and_raw_value(self):
        out = render.delivery_json({"delivery": _delivery()}, _meta())
        rows = out["mix"]["rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {"label": "bug", "value": 5, "pct": 25.0,
                                    "drill": {"drill": "issue", "category": "bug"}})

    def test_flow_stages_with_pct_and_bar(self):
        out = render.delivery_json({"delivery": _delivery()}, _meta())
        flow = out["flow"]
        self.assertTrue(flow["hasData"])
        self.assertEqual(flow["total"], 10)
        backlog, in_progress, done = flow["stages"]
        # pre-formatted one-decimal STRINGS ("30.0", not 30) — see
        # render.py's delivery_json() comment: Jinja's `|round` (no precision
        # arg) still returns a float, rendered with its trailing ".0".
        self.assertEqual(backlog["pct"], "30.0")
        self.assertEqual(in_progress["pct"], "50.0")
        self.assertEqual(in_progress["barPct"], 100.0)     # max stage -> full bar
        self.assertEqual(done["pct"], "20.0")

    def test_no_flow_data(self):
        out = render.delivery_json({"delivery": _delivery(flow_stages=[], flow_total=0)}, _meta())
        self.assertFalse(out["flow"]["hasData"])

    def test_raw_subs_and_revert_value_never_get_thousands_separators(self):
        # The Jinja macros build these via `~` string concat / a bare kpi_tile
        # value (NOT the |num filter) — templates/panels/03_delivery.j2's
        # close-rate/CI-pass-rate subs and the reverts tile's value — so a
        # 4-digit count must render WITHOUT a comma, unlike every |num'd tile.
        out = render.delivery_json({"delivery": _delivery(
            issues_closed=1215, ci_gate_runs=2048, pr_reverts=1500)}, _meta())
        self.assertEqual(out["kpis"][1]["sub"], "1215 closed")
        self.assertEqual(out["ci"][0]["sub"], "2048 gate runs")
        self.assertEqual(out["pr"][6]["value"], "1500")


class DeliveryApiEndpointTest(unittest.TestCase):
    """Same ThreadingHTTPServer + isolated REPORT_DB harness as
    OverviewApiEndpointTest/TrendApiEndpointTest — the validation paths below
    need no real collected data."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._env = patch.dict(os.environ, {"REPORT_DB": str(Path(self._tmp.name) / "t.db")})
        self._env.start()
        import server
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self._env.stop()
        self._tmp.cleanup()

    def _get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_no_data_returns_404(self):
        status, body = self._get("/api/report/delivery")
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "no collected data")

    def test_invalid_scope_returns_400(self):
        status, body = self._get("/api/report/delivery?slice=notalevel:x")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid scope")

    def test_invalid_dates_return_400(self):
        status, body = self._get("/api/report/delivery?from=not-a-date")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_invalid_preset_returns_400(self):
        status, body = self._get("/api/report/delivery?p=nonsense")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid p")

    def test_from_after_to_returns_400(self):
        status, body = self._get("/api/report/delivery?from=2026-01-10&to=2026-01-01")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "from is after to")


if __name__ == "__main__":
    unittest.main()
