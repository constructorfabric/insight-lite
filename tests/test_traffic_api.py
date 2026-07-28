"""Tests for the /traffic React route (see
docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P9):

- render.traffic_json(): a pure function fed a `pr` dict carrying a `traffic`
  block (shaped like store.aggregate()'s / build_model()'s windowed clone/view
  rollup) + a `meta` dict carrying the (period-invariant) envelope AND the
  all-time scenario data (contributors / non_contributors / external_
  contributors / stars / forks / platform_repos / emails_by_login) that
  server.py threads through from build_model(). Same convention as
  tests/test_elements_api.py's ElementsJson* tests.
- GET /api/report/traffic: the same ThreadingHTTPServer + isolated REPORT_DB
  harness as ElementsApiEndpointTest — validation paths that don't need real
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


def _traffic():
    return {
        "total_clones": 1400, "unique_cloners": 100,
        "total_views": 5000, "total_visitors": 250,
        "n_repos": 2, "n_no_access": 1, "daily_max": 10,
        "rows": [
            {"name": "insight", "clones": 1000, "uniques": 50, "views": 4000, "visitors": 200,
             "daily": [{"date": "2026-07-01", "count": 10, "uniques": 3},
                       {"date": "2026-07-02", "count": 5, "uniques": 2}],
             "paths": [{"path": "/your-org/insight/pulls", "views": 120, "uniques": 30}],
             "contributors": 0},
            {"name": "studio", "clones": 400, "uniques": 0, "views": 1000, "visitors": 50,
             "daily": [], "paths": [], "contributors": 0},
        ],
    }


def _meta(**over):
    m = {
        "org": "acme-org", "all_time": True, "window_start": "2008-01-01",
        "lookback_days": 100, "generated": "2026-07-11T16:10:05Z",
        "all_label": "All-time", "scope_targets": {"element": ["Insight"]},
        "data_quality": {"api_rate_limited": False, "api_reset": None},
        "window_labels": ["30d", "all"],
        # all-time scenario data (server threads these from build_model())
        "contributors": [
            {"login": "ainetx", "is_member": True, "value": 200, "commits": 100, "prs": 40, "specs": 5},
            {"login": "octocat", "is_member": False, "value": 50, "commits": 30, "prs": 10, "specs": 0},
        ],
        "members_contrib": [
            {"login": "ainetx", "is_member": True, "value": 200, "commits": 100, "prs": 40, "specs": 5},
        ],
        "external_contributors": [
            {"login": "octocat", "is_member": False, "value": 50, "commits": 30, "prs": 10, "specs": 0},
        ],
        "non_contributors": [
            {"login": "lurker", "is_member": False, "forked": ["insight", "studio"]},
        ],
        "total_stars": 321, "total_forks": 77,
        "platform_repos": [{"name": "insight"}, {"name": "studio"}, {"name": "dna"}],
        "emails_by_login": {"ainetx": "a@acme.org", "octocat": "o@ext.com"},
    }
    m.update(over)
    return m


class TrafficJsonPanelTest(unittest.TestCase):
    def test_panel_present_and_preformatted(self):
        out = render.traffic_json({"traffic": _traffic()}, _meta())
        t = out["traffic"]
        self.assertTrue(t["present"])
        self.assertFalse(t["windowed"])
        # counts pre-formatted with thousands separators (|num)
        self.assertEqual(t["views"], "5,000")
        self.assertEqual(t["clones"], "1,400")
        self.assertEqual(t["cloners"], "100")
        self.assertEqual(t["nNoAccess"], 1)

    def test_row_ci_ratio_and_daily_bars(self):
        out = render.traffic_json({"traffic": _traffic()}, _meta())
        rows = out["traffic"]["rows"]
        # insight: 1000 clones / 50 uniques -> 20.0× CI
        self.assertEqual(rows[0]["ci"], "20.0")
        # studio: 0 uniques -> the em-dash (macro's `… if r.uniques else '—'`)
        self.assertEqual(rows[1]["ci"], "—")
        # daily bar height normalised to daily_max=10 -> 100 and 50
        self.assertEqual(rows[0]["daily"][0]["h"], 100)
        self.assertEqual(rows[0]["daily"][1]["h"], 50)
        self.assertEqual(rows[0]["daily"][0]["tip"], "2026-07-01: 10 clones / 3 cloners")

    def test_path_display_drops_org_repo_segments(self):
        out = render.traffic_json({"traffic": _traffic()}, _meta())
        p = out["traffic"]["rows"][0]["paths"][0]
        # '/your-org/insight/pulls'.split('/')[3:] -> ['pulls']
        self.assertEqual(p["text"], "pulls")
        self.assertEqual(p["views"], 120)
        self.assertEqual(p["tip"], "30 unique viewers")

    def test_windowed_panel(self):
        tr = _traffic()
        tr["windowed"] = True
        tr["since"] = "2026-01-01"
        out = render.traffic_json({"traffic": tr}, _meta())
        self.assertTrue(out["traffic"]["windowed"])
        self.assertEqual(out["traffic"]["since"], "2026-01-01")

    def test_empty_traffic_degrades(self):
        out = render.traffic_json({}, _meta())
        self.assertFalse(out["traffic"]["present"])
        self.assertEqual(out["traffic"]["rows"], [])
        self.assertTrue(out["ok"])


class TrafficJsonScenariosTest(unittest.TestCase):
    def test_scenario_counts_and_bars(self):
        out = render.traffic_json({"traffic": _traffic()}, _meta())
        s = out["scenarios"]
        self.assertEqual(s["contributorsCount"], 2)
        self.assertEqual(s["membersCount"], 1)
        self.assertEqual(s["externalCount"], 1)
        # bar normalised to the top contributor (value 200): 100 and 25
        self.assertEqual(s["contributors"][0]["bar"], 100)
        self.assertEqual(s["contributors"][1]["bar"], 25)
        self.assertEqual(s["contributors"][0]["value"], 200)
        self.assertTrue(s["contributors"][0]["isMember"])
        self.assertFalse(s["contributors"][1]["isMember"])
        self.assertEqual(s["contributors"][0]["email"], "a@acme.org")

    def test_non_contributors_and_totals(self):
        out = render.traffic_json({"traffic": _traffic()}, _meta())
        s = out["scenarios"]
        self.assertEqual(s["nonContributors"][0]["login"], "lurker")
        self.assertEqual(s["nonContributors"][0]["forked"], ["insight", "studio"])
        self.assertEqual(s["totalStars"], 321)
        self.assertEqual(s["totalForks"], 77)
        self.assertEqual(s["platformReposCount"], 3)

    def test_external_contributors_chips(self):
        out = render.traffic_json({"traffic": _traffic()}, _meta())
        ext = out["externalContributors"]
        self.assertEqual(len(ext), 1)
        self.assertEqual(ext[0]["login"], "octocat")
        self.assertEqual(ext[0]["value"], 50)
        self.assertEqual(ext[0]["email"], "o@ext.com")

    def test_empty_scenarios_degrade(self):
        out = render.traffic_json({}, {})
        self.assertEqual(out["scenarios"]["contributorsCount"], 0)
        self.assertEqual(out["scenarios"]["contributors"], [])
        self.assertEqual(out["externalContributors"], [])
        self.assertTrue(out["ok"])


class TrafficJsonEnvelopeTest(unittest.TestCase):
    def test_full_payload_shape(self):
        out = render.traffic_json({"traffic": _traffic()}, _meta())
        self.assertTrue(out["ok"])
        self.assertEqual(out["meta"]["org"], "acme-org")
        self.assertEqual(out["meta"]["generatedText"], "2026-07-11 16:10")
        self.assertEqual(out["period"], {"preset": "all", "label": "All-time", "from": None, "to": None})
        self.assertEqual(out["periodPresets"], [{"key": "30d", "label": "30 days"},
                                                {"key": "all", "label": "All-time"}])
        self.assertEqual(out["scopeTargets"], {"element": ["Insight"]})
        self.assertFalse(out["dataQuality"]["apiRateLimited"])


class TrafficApiEndpointTest(unittest.TestCase):
    """Same ThreadingHTTPServer + isolated REPORT_DB harness as
    ElementsApiEndpointTest (tests/test_elements_api.py) — the validation paths
    below need no real collected data."""

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
        status, body = self._get("/api/report/traffic")
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "no collected data")

    def test_invalid_scope_returns_400(self):
        status, body = self._get("/api/report/traffic?slice=notalevel:x")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid scope")

    def test_invalid_dates_return_400(self):
        status, body = self._get("/api/report/traffic?from=not-a-date")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_invalid_preset_returns_400(self):
        status, body = self._get("/api/report/traffic?p=nonsense")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid p")

    def test_from_after_to_returns_400(self):
        status, body = self._get("/api/report/traffic?from=2026-01-10&to=2026-01-01")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "from is after to")


if __name__ == "__main__":
    unittest.main()
