"""Tests for the /ai-tools React route (see
docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P10 — the LAST
report view; the monolith's `fabric` mode, renamed ai-tools per the redirect
table):

- render.ai_tools_json(): a pure function fed a `pr` dict carrying the
  period-scoped panels (`ai_usage` + the `bots` MINI stats) + a `meta` dict
  carrying the (period-invariant) envelope AND all the all-time fabric data
  (studio_prov / gears_usage / fabric_trackers / cpt_people / cpt_by_company /
  fabric_company / fabric_people / bots_all) that server.py threads through from
  build_model(). Same convention as tests/test_traffic_api.py.
- GET /api/report/ai-tools: the same ThreadingHTTPServer + isolated REPORT_DB
  harness as TrafficApiEndpointTest — validation paths that don't need real
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


def _pr():
    """A period-scoped block shaped like store.aggregate() / all_block: the
    AI-usage panel + the bots MINI stats are the only period-scoped fabric
    panels."""
    return {
        "ai_usage": {
            "any_commits": 1055, "total_commits": 11892, "pct": 8.9,
            "tools": [
                {"tool": "Assistant (commit mention)", "commits": 718, "loc": 893392, "pct": 6.0},
                {"tool": "the in-house assistant", "commits": 169, "loc": 39362, "pct": 1.4},
                {"tool": "BigTool", "commits": 5000, "loc": 100, "pct": 42.0},
            ],
        },
        "bots": {"count": 10, "commits": 226, "additions": 36056, "reviews": None, "windowed": True},
    }


def _marker_block():
    return {
        "markers": ["Alpha", "Beta"],
        "precision": {"Alpha": "exact", "Beta": "heuristic"},
        "totals": {"Alpha": {"files": 15, "lines": 150}, "Beta": {"files": 3, "lines": 9}},
        "by_repo": {
            "org/insight": {"Alpha": {"files": 10, "lines": 100}, "Beta": {"files": 0, "lines": 0}},
            "org/studio": {"Alpha": {"files": 5, "lines": 50}},   # Beta missing -> 0/0
        },
    }


def _meta(**over):
    m = {
        "org": "acme-org", "all_time": True, "window_start": "2008-01-01",
        "lookback_days": 100, "generated": "2026-07-11T16:10:05Z",
        "all_label": "All-time", "scope_targets": {"element": ["Insight"]},
        "data_quality": {"api_rate_limited": False, "api_reset": None},
        "window_labels": ["30d", "all"],
        # all-time fabric data (server threads these from build_model())
        "studio_prov": _marker_block(),
        "gears_usage": {"markers": ["G1"], "precision": {"G1": "exact"},
                        "totals": {"G1": {"files": 7, "lines": 70}},
                        "by_repo": {"org/insight": {"G1": {"files": 7, "lines": 70}}}},
        "fabric_trackers": {
            "Kit installed": {"markers": ["Kit"], "precision": {"Kit": "exact"},
                              "totals": {"Kit": {"files": 4, "lines": 4}},
                              "by_repo": {"org/insight": {"Kit": {"files": 4, "lines": 4}}}},
            "Empty tracker": {"markers": []},   # skipped (no markers)
        },
        "cpt_people": [{"login": "ainetx", "name": "Max", "company": "Example Inc", "lines": 18974}],
        "cpt_by_company": [
            {"company": "Example Inc", "lines": 32326, "color": "#8250df"},
            {"company": "Constructor", "lines": 6179, "color": "#0969da"},
            {"company": "Partner Ltd", "lines": 2062, "color": "#1a7f37"},
        ],
        "fabric_company": [
            {"company": "Example Inc", "commits": 7446, "ai_commits": 788, "ai_pct": 10.6, "cpt_lines": 32326},
        ],
        "fabric_people": [
            {"login": "ainetx", "company": "Example Inc", "ai_commits": 614, "ai_pct": 29.0, "cpt_lines": 18974},
            {"login": "octocat", "company": "Other", "ai_commits": 3, "ai_pct": 100.0, "cpt_lines": 0},
        ],
        "bots_all": {"rows": [
            {"login": "github-actions[bot]", "kind": "commits", "commits": 138, "additions": 19248,
             "ai_commits": 0, "reviews_given": 0, "repos": ["a", "b"], "emails": ["ga@x.com"]},
            {"login": "coderabbitai", "kind": "reviews", "commits": 0, "additions": 0,
             "ai_commits": 0, "reviews_given": 3757, "repos": [], "emails": []},
        ]},
    }
    m.update(over)
    return m


class AiToolsJsonAiUsageTest(unittest.TestCase):
    def test_ai_usage_preformatted(self):
        out = render.ai_tools_json(_pr(), _meta())
        ai = out["aiUsage"]
        # any/total commits are raw ints (the monolith hint prints them unfiltered)
        self.assertEqual(ai["anyCommits"], 1055)
        self.assertEqual(ai["totalCommits"], 11892)
        self.assertEqual(ai["pct"], "8.9")
        self.assertTrue(ai["anyDrill"])
        self.assertTrue(ai["toolsAvailable"])

    def test_tool_rows_preformatted(self):
        out = render.ai_tools_json(_pr(), _meta())
        t0 = out["aiUsage"]["tools"][0]
        self.assertEqual(t0["tool"], "Assistant (commit mention)")
        self.assertEqual(t0["commits"], "718")        # |num
        self.assertEqual(t0["commitsRaw"], 718)
        self.assertEqual(t0["pctStr"], "6")           # |pct strips ".0"
        self.assertEqual(t0["pctRaw"], 6.0)
        self.assertEqual(t0["loc"], "893.4K")         # |loc
        # the >=8 label threshold reads pctRaw (BigTool 42.0 qualifies)
        big = out["aiUsage"]["tools"][2]
        self.assertEqual(big["commits"], "5,000")
        self.assertGreaterEqual(big["pctRaw"], 8)

    def test_tools_none_marks_unavailable(self):
        pr = _pr()
        pr["ai_usage"]["tools"] = None
        out = render.ai_tools_json(pr, _meta())
        self.assertFalse(out["aiUsage"]["toolsAvailable"])
        self.assertEqual(out["aiUsage"]["tools"], [])

    def test_empty_ai_usage_degrades(self):
        out = render.ai_tools_json({}, _meta())
        ai = out["aiUsage"]
        self.assertEqual(ai["anyCommits"], 0)
        self.assertFalse(ai["anyDrill"])
        self.assertFalse(ai["toolsAvailable"])
        self.assertTrue(out["ok"])


class AiToolsJsonBotsTest(unittest.TestCase):
    def test_mini_period_scoped_and_windowed(self):
        out = render.ai_tools_json(_pr(), _meta())
        bm = out["botsMini"]
        self.assertEqual(bm["count"], 10)
        self.assertEqual(bm["commits"], "226")        # |num
        self.assertEqual(bm["additions"], "36.1K")    # |loc
        self.assertIsNone(bm["reviews"])              # reviews None -> "—" in the UI
        self.assertTrue(bm["windowed"])

    def test_detail_rows_all_time_from_meta(self):
        out = render.ai_tools_json(_pr(), _meta())
        rows = out["botRows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["login"], "github-actions[bot]")
        self.assertEqual(rows[0]["repos"], "a, b")
        self.assertEqual(rows[0]["emails"], "ga@x.com")
        # no repos -> the em-dash placeholder (the macro's `if b.repos else '—'`)
        self.assertEqual(rows[1]["repos"], "—")
        self.assertEqual(rows[1]["reviews_given"], 3757)


class AiToolsJsonMarkerTablesTest(unittest.TestCase):
    def test_studio_provenance_table_and_badges(self):
        out = render.ai_tools_json(_pr(), _meta())
        sp = out["studioProv"]
        self.assertTrue(sp["present"])
        self.assertEqual(sp["table"]["markers"], ["Alpha", "Beta"])
        self.assertEqual(sp["table"]["badges"],
                         [{"marker": "Alpha", "prec": "exact"}, {"marker": "Beta", "prec": "heuristic"}])
        # mini carries raw file/line ints per marker
        self.assertEqual(sp["mini"][0], {"marker": "Alpha", "files": 15, "lines": 150})
        # rows: repo shows last path segment; missing marker -> 0/0
        r = sp["table"]["rows"]
        self.assertEqual(r[0]["repo"], "insight")
        self.assertEqual(r[0]["cells"], [{"files": 10, "lines": 100}, {"files": 0, "lines": 0}])
        self.assertEqual(r[1]["repo"], "studio")
        self.assertEqual(r[1]["cells"], [{"files": 5, "lines": 50}, {"files": 0, "lines": 0}])

    def test_cpt_by_company_split(self):
        out = render.ai_tools_json(_pr(), _meta())
        sp = out["studioProv"]
        self.assertTrue(sp["cptPresent"])
        segs = sp["cptSegments"]
        # 32326 / (32326+6179+2062=40567) -> 79.7 (round 1); label shown (>=9)
        self.assertEqual(segs[0]["width"], "79.7")
        self.assertEqual(segs[0]["label"], "Example Inc")
        # Partner Ltd 2062/40567 = 5.08% -> below 9 -> no label
        self.assertEqual(segs[2]["label"], "")
        self.assertEqual(segs[2]["width"], "5.1")
        self.assertEqual(sp["cptPeopleCount"], 1)

    def test_gears_and_trackers(self):
        out = render.ai_tools_json(_pr(), _meta())
        self.assertTrue(out["gearsUsage"]["present"])
        self.assertEqual(out["gearsUsage"]["repoCount"], 1)
        # only markers-present trackers render (Empty tracker skipped)
        self.assertEqual([t["name"] for t in out["trackers"]], ["Kit installed"])
        self.assertEqual(out["trackers"][0]["repoCount"], 1)
        self.assertEqual(out["trackers"][0]["mini"], [{"marker": "Kit", "files": 4}])

    def test_studio_absent_when_no_markers(self):
        out = render.ai_tools_json(_pr(), _meta(studio_prov={"markers": []}))
        self.assertFalse(out["studioProv"]["present"])


class AiToolsJsonFabricUsageTest(unittest.TestCase):
    def test_company_rows_passthrough(self):
        out = render.ai_tools_json(_pr(), _meta())
        self.assertEqual(out["fabricCompany"][0]["company"], "Example Inc")
        self.assertEqual(out["fabricCompany"][0]["ai_pct"], 10.6)

    def test_people_ai_pct_preformatted_string(self):
        out = render.ai_tools_json(_pr(), _meta())
        self.assertEqual(out["fabricPeopleCount"], 2)
        p = out["fabricPeople"]
        # AI% rendered raw ({{ v }}%) -> str(float) keeps trailing ".0"
        self.assertEqual(p[0]["aiPctStr"], "29.0")
        self.assertEqual(p[1]["aiPctStr"], "100.0")


class AiToolsJsonEnvelopeTest(unittest.TestCase):
    def test_full_payload_shape(self):
        out = render.ai_tools_json(_pr(), _meta())
        self.assertTrue(out["ok"])
        self.assertEqual(out["meta"]["org"], "acme-org")
        self.assertEqual(out["meta"]["generatedText"], "2026-07-11 16:10")
        self.assertEqual(out["period"], {"preset": "all", "label": "All-time", "from": None, "to": None})
        self.assertEqual(out["periodPresets"], [{"key": "30d", "label": "30 days"},
                                                {"key": "all", "label": "All-time"}])
        self.assertEqual(out["scopeTargets"], {"element": ["Insight"]})
        self.assertFalse(out["dataQuality"]["apiRateLimited"])

    def test_empty_meta_degrades(self):
        out = render.ai_tools_json({}, {})
        self.assertTrue(out["ok"])
        self.assertFalse(out["studioProv"]["present"])
        self.assertFalse(out["gearsUsage"]["present"])
        self.assertEqual(out["trackers"], [])
        self.assertEqual(out["fabricCompany"], [])
        self.assertEqual(out["botRows"], [])


class AiToolsApiEndpointTest(unittest.TestCase):
    """Same ThreadingHTTPServer + isolated REPORT_DB harness as
    TrafficApiEndpointTest (tests/test_traffic_api.py) — the validation paths
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
        status, body = self._get("/api/report/ai-tools")
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "no collected data")

    def test_invalid_scope_returns_400(self):
        status, body = self._get("/api/report/ai-tools?slice=notalevel:x")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid scope")

    def test_invalid_dates_return_400(self):
        status, body = self._get("/api/report/ai-tools?from=not-a-date")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_invalid_preset_returns_400(self):
        status, body = self._get("/api/report/ai-tools?p=nonsense")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid p")

    def test_from_after_to_returns_400(self):
        status, body = self._get("/api/report/ai-tools?from=2026-01-10&to=2026-01-01")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "from is after to")


if __name__ == "__main__":
    unittest.main()
