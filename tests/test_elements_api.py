"""Tests for the /elements React route (see
docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P8):

- render.elements_json(): a pure function fed a `pr` dict carrying
  `element_rows` (shaped like store.aggregate()'s / build_model()'s per-element
  rollup) + a `meta` dict for the (period-invariant) envelope. Same convention
  as tests/test_repositories_api.py's ReposJson* tests.
- GET /api/report/elements: the same ThreadingHTTPServer + isolated REPORT_DB
  harness as RepositoriesApiEndpointTest — validation paths that don't need real
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


def _rows():
    return [
        {"element": "Insight", "code_kloc": 10.0, "spec_kloc": 0.2,
         "code_loc": 10000, "spec_loc": 200, "repos": 3,
         "people_members": 6, "people_external": 2,
         "commits_window": 120, "prs_opened_window": 8, "prs_merged_window": 5,
         "median_ttm_h": 24.0, "ai_pct": 12.0},
        {"element": "Studio", "code_kloc": 5.0, "spec_kloc": 0.0,
         "code_loc": 5000, "spec_loc": 0, "repos": 1,
         "people_members": 0, "people_external": 0,
         "commits_window": 0, "prs_opened_window": 0, "prs_merged_window": 0,
         "median_ttm_h": None, "ai_pct": 0.0},
    ]


def _meta(**over):
    m = {
        "org": "acme-org", "all_time": True, "window_start": "2008-01-01",
        "lookback_days": 100, "generated": "2026-07-11T16:10:05Z",
        "all_label": "All-time", "scope_targets": {"element": ["Insight"]},
        "data_quality": {"api_rate_limited": False, "api_reset": None},
        "window_labels": ["30d", "all"],
    }
    m.update(over)
    return m


class ElementsJsonRowsTest(unittest.TestCase):
    def test_rows_pass_through(self):
        out = render.elements_json({"element_rows": _rows()}, _meta())
        self.assertEqual(len(out["elementRows"]), 2)
        self.assertEqual(out["elementRows"][0]["element"], "Insight")
        self.assertEqual(out["elementRows"][0]["code_loc"], 10000)

    def test_code_bar_normalised_to_kmax(self):
        out = render.elements_json({"element_rows": _rows()}, _meta())
        # kmax = max(code_kloc) = 10.0 -> Insight 10/10*100 = 100, Studio 5/10*100 = 50
        self.assertEqual(out["elementRows"][0]["_code_bar"], 100.0)
        self.assertEqual(out["elementRows"][1]["_code_bar"], 50.0)

    def test_scope_and_colour_derived(self):
        out = render.elements_json({"element_rows": _rows()}, _meta())
        self.assertEqual(out["elementRows"][0]["_scope"], "element:Insight")
        # element_color mirrors render._element_color (the ecolor() global)
        self.assertEqual(out["elementRows"][0]["element_color"], render._element_color("Insight"))

    def test_median_ttm_preformatted_string_or_none(self):
        out = render.elements_json({"element_rows": _rows()}, _meta())
        # raw kind renders `{{ v }}` — a whole-number rounded median keeps ".0"
        self.assertEqual(out["elementRows"][0]["median_ttm_h"], "24.0")
        # no merges -> None -> the dash cell
        self.assertIsNone(out["elementRows"][1]["median_ttm_h"])

    def test_empty_rows_degrade(self):
        out = render.elements_json({}, _meta())
        self.assertEqual(out["elementRows"], [])
        self.assertTrue(out["ok"])


class ElementsJsonEnvelopeTest(unittest.TestCase):
    def test_full_payload_shape(self):
        out = render.elements_json({"element_rows": _rows()}, _meta())
        self.assertTrue(out["ok"])
        self.assertEqual(out["meta"]["org"], "acme-org")
        self.assertEqual(out["meta"]["generatedText"], "2026-07-11 16:10")
        self.assertEqual(out["period"], {"preset": "all", "label": "All-time", "from": None, "to": None})
        self.assertEqual(out["periodPresets"], [{"key": "30d", "label": "30 days"},
                                                {"key": "all", "label": "All-time"}])
        self.assertEqual(out["scopeTargets"], {"element": ["Insight"]})
        self.assertFalse(out["dataQuality"]["apiRateLimited"])


class ElementsApiEndpointTest(unittest.TestCase):
    """Same ThreadingHTTPServer + isolated REPORT_DB harness as
    RepositoriesApiEndpointTest (tests/test_repositories_api.py) — the validation
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
        status, body = self._get("/api/report/elements")
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "no collected data")

    def test_invalid_scope_returns_400(self):
        status, body = self._get("/api/report/elements?slice=notalevel:x")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid scope")

    def test_invalid_dates_return_400(self):
        status, body = self._get("/api/report/elements?from=not-a-date")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_invalid_preset_returns_400(self):
        status, body = self._get("/api/report/elements?p=nonsense")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid p")

    def test_from_after_to_returns_400(self):
        status, body = self._get("/api/report/elements?from=2026-01-10&to=2026-01-01")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "from is after to")


if __name__ == "__main__":
    unittest.main()
