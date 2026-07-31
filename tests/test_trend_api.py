"""Tests for the /trend React route (see
docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P2):

- render.trend_json(): a pure function fed a hand-built `pr` dict shaped like
  store.aggregate(..., trend_gran=, trend_dim=)'s return (pr['ctrend'] =
  store.trend_block()'s shape, pr['company_rows'] for colour resolution) —
  same convention as tests/test_overview_api.py's OverviewJsonTest.
- GET /api/report/trend: the same ThreadingHTTPServer + isolated REPORT_DB
  harness as OverviewApiEndpointTest — validation paths that don't need real
  collected data.
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


def _ct(**over):
    ct = {
        "points": 3, "dates": ["2026-01", "2026-02", "2026-03"],
        "gran": "month", "gran_req": "auto", "dim": "company",
        "dims": [{"key": "company", "label": "Company"}, {"key": "work_type", "label": "Work type"},
                 {"key": "repo_type", "label": "Repo type"}, {"key": "element", "label": "Element"}],
        "commit_rows": [{"company": "Acme", "key": "Acme", "vals": [1, 2, 3]}],
        "loc_rows": [{"company": "Acme", "key": "Acme", "vals": [10, 20, 30]}],
        "throughput": {"opened": [1, 2, 3], "merged": [0, 1, 2], "ttm": [None, None, None]},
        "contributors": [1, 2, 3],
    }
    ct.update(over)
    return ct


def _pr(**over):
    pr = {"ctrend": _ct(), "company_rows": [{"company": "Acme", "color": "#123456"}]}
    pr.update(over)
    return pr


def _meta(**over):
    meta = {
        "org": "acme-org", "all_time": True, "window_start": "2008-01-01", "lookback_days": 100,
        "generated": "2026-07-22T16:10:05Z", "all_label": "All-time",
        "scope_targets": {"org": ["acme-org"]}, "window_labels": ["30d", "all"],
    }
    meta.update(over)
    return meta


class TrendJsonTest(unittest.TestCase):
    def test_no_data_when_points_zero(self):
        out = render.trend_json(_pr(ctrend={"points": 0}), _meta())
        self.assertTrue(out["ok"])
        self.assertIsNone(out["data"])

    def test_no_data_when_ctrend_missing(self):
        out = render.trend_json({}, _meta())
        self.assertTrue(out["ok"])
        self.assertIsNone(out["data"])

    def test_envelope_shape_matches_overview_convention(self):
        out = render.trend_json(_pr(), _meta())
        self.assertTrue(out["ok"])
        self.assertEqual(out["meta"]["org"], "acme-org")
        self.assertEqual(out["meta"]["generatedText"], "2026-07-22 16:10")
        self.assertEqual(out["period"], {"preset": "all", "label": "All-time", "from": None, "to": None})
        self.assertEqual(out["periodPresets"], [{"key": "30d", "label": "30 days"},
                                                 {"key": "all", "label": "All-time"}])
        self.assertEqual(out["scopeTargets"], {"org": ["acme-org"]})

    def test_data_shape_and_dim_label(self):
        d = render.trend_json(_pr(), _meta())["data"]
        self.assertIsNotNone(d)
        self.assertEqual(d["dates"], ["2026-01", "2026-02", "2026-03"])
        self.assertEqual(d["dim"], "company")
        self.assertEqual(d["dimlabel"], "company")
        self.assertEqual(d["gran"], "month")
        self.assertEqual(d["granreq"], "auto")
        self.assertEqual(d["noun"], "month")
        self.assertEqual(d["points"], 3)
        self.assertEqual(len(d["dims"]), 4)
        self.assertEqual(d["legend"], [{"company": "Acme", "color": "#123456"}])

    def test_charts_present_and_ttm_null_when_no_samples(self):
        d = render.trend_json(_pr(), _meta())["data"]
        # Each is a chart_data envelope; the two per-company ones stack, the rest do
        # not, and every series must line up with the shared x axis or the picture
        # skews without erroring.
        for key in ("commitChart", "locChart", "throughputChart", "contributorsChart"):
            chart = d[key]
            self.assertIsNotNone(chart, key)
            self.assertTrue(chart["series"], key)
            for s in chart["series"]:
                self.assertEqual(len(s["vals"]), len(chart["dates"]), f"{key}/{s['key']}")
        self.assertTrue(d["commitChart"]["stacked"])
        self.assertTrue(d["locChart"]["stacked"])
        self.assertFalse(d["throughputChart"]["stacked"])
        self.assertTrue(d["contributorsChart"]["areaFirst"])
        self.assertIsNone(d["ttmChart"])      # every ttm sample was None

    def test_ttm_present_when_any_sample(self):
        pr = _pr(ctrend=_ct(throughput={"opened": [1, 2, 3], "merged": [0, 1, 2], "ttm": [None, 5.0, None]}))
        d = render.trend_json(pr, _meta())["data"]
        self.assertIsNotNone(d["ttmChart"])
        self.assertEqual(d["ttmChart"]["unit"], "hours")

    def test_dim_label_switches_with_active_dim(self):
        pr = _pr(ctrend=_ct(dim="work_type",
                             commit_rows=[{"company": "feat", "key": "feat", "vals": [1, 2, 3]}]))
        d = render.trend_json(pr, _meta())["data"]
        self.assertEqual(d["dim"], "work_type")
        self.assertEqual(d["dimlabel"], "work type")

    def test_gran_req_falls_back_to_resolved_gran_noun(self):
        pr = _pr(ctrend=_ct(gran="week", gran_req="auto"))
        d = render.trend_json(pr, _meta())["data"]
        self.assertEqual(d["noun"], "week")


class TrendApiEndpointTest(unittest.TestCase):
    """Same ThreadingHTTPServer + isolated REPORT_DB harness as
    OverviewApiEndpointTest (tests/test_overview_api.py) — the validation
    paths below need no real collected data."""

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
        status, body = self._get("/api/report/trend")
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "no collected data")

    def test_invalid_scope_returns_400(self):
        status, body = self._get("/api/report/trend?slice=notalevel:x")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid scope")

    def test_invalid_dates_return_400(self):
        status, body = self._get("/api/report/trend?from=not-a-date")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_invalid_preset_returns_400(self):
        status, body = self._get("/api/report/trend?p=nonsense")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid p")

    def test_from_after_to_returns_400(self):
        status, body = self._get("/api/report/trend?from=2026-01-10&to=2026-01-01")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "from is after to")

    def test_invalid_gran_returns_400(self):
        status, body = self._get("/api/report/trend?gran=nonsense")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid gran")

    def test_invalid_dim_returns_400(self):
        status, body = self._get("/api/report/trend?dim=nonsense")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid dim")


if __name__ == "__main__":
    unittest.main()
