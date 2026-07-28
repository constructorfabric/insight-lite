"""Tests for the /repositories React route (see
docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P7):

- render.repositories_json(): a pure function fed a hand-built `pr` dict
  (period-scoped split block, shaped like store.aggregate()'s return) + a
  `meta` dict carrying the ALL-TIME repo inventory (repo_summary / repo_rows /
  unclassified — from build_model()). Same convention as
  tests/test_people_api.py's PeopleJson* tests.
- GET /api/report/repositories: the same ThreadingHTTPServer + isolated
  REPORT_DB harness as PeopleApiEndpointTest — validation paths that don't need
  real collected data.
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


def _split(**over):
    s = {
        "types": [
            {"id": "platform", "name": "Platform", "color": "#5b5bf0", "commits": 30, "prs": 6, "loc": 3000},
            {"id": "app", "name": "Apps", "color": "#10b981", "commits": 10, "prs": 2, "loc": 1000},
        ],
        "commits_total": 40, "prs_total": 8, "loc_total": 4000,
    }
    s.update(over)
    return s


def _meta(**over):
    m = {
        "repo_summary": {
            "total": 12, "distinct": 10, "primary": 8, "legacy_dup": 2, "primary_org": "acme-org",
            "platform": 4, "app": 5, "unclassified": 1, "missing_traffic": 3, "legacy_only": 2,
        },
        "repo_rows": [
            {"full_name": "acme-org/core", "org": "acme-org", "name": "core", "classification": "platform",
             "unclassified": False, "stars": 12, "forks": 4, "contributors": 6, "traffic_access": True,
             "clones": 20, "uniques": 8, "element": "Core", "code_loc": 5000, "spec_loc": 200,
             "total_loc": 5200, "legacy_only": False},
            {"full_name": "acme-org/mystery", "org": "acme-org", "name": "mystery", "classification": "unclassified",
             "unclassified": True, "stars": 0, "forks": 0, "contributors": 1, "traffic_access": False,
             "clones": 0, "uniques": 0, "element": "Other", "code_loc": None, "spec_loc": None,
             "total_loc": None, "legacy_only": True},
        ],
        "unclassified": ["mystery"],
    }
    m.update(over)
    return m


class ReposJsonCoverageTest(unittest.TestCase):
    def test_repo_summary_camelcased(self):
        out = render.repositories_json({}, _meta())
        rs = out["repoSummary"]
        self.assertEqual(rs["distinct"], 10)
        self.assertEqual(rs["primaryOrg"], "acme-org")
        self.assertEqual(rs["legacyOnly"], 2)
        self.assertEqual(rs["legacyDup"], 2)
        self.assertEqual(rs["missingTraffic"], 3)
        self.assertEqual(rs["total"], 12)

    def test_repo_rows_pass_through(self):
        out = render.repositories_json({}, _meta())
        self.assertEqual(len(out["repoRows"]), 2)
        self.assertEqual(out["repoRows"][0]["name"], "core")
        self.assertIsNone(out["repoRows"][1]["code_loc"])   # dash-column source stays None

    def test_unclassified_list(self):
        out = render.repositories_json({}, _meta())
        self.assertEqual(out["unclassified"], ["mystery"])

    def test_empty_meta_degrades_to_zeros(self):
        out = render.repositories_json({}, {})
        self.assertEqual(out["repoSummary"]["distinct"], 0)
        self.assertEqual(out["repoRows"], [])
        self.assertEqual(out["unclassified"], [])
        self.assertFalse(out["split"]["present"])


class ReposJsonSplitTest(unittest.TestCase):
    def test_present_and_three_bars(self):
        out = render.repositories_json({"split": _split()}, _meta())
        self.assertTrue(out["split"]["present"])
        bars = out["split"]["bars"]
        self.assertEqual([b["sub"] for b in bars],
                         ["Commits by type", "Pull requests by type", "Meaningful LOC by type"])
        self.assertEqual([b["drill"] for b in bars], ["commit", "pr", "commit"])

    def test_not_present_without_commits_total(self):
        out = render.repositories_json({"split": _split(commits_total=0)}, _meta())
        self.assertFalse(out["split"]["present"])
        self.assertEqual(out["split"]["bars"], [])

    def test_preformatted_widths_and_legend(self):
        out = render.repositories_json({"split": _split()}, _meta())
        commits = out["split"]["bars"][0]
        # width = (100*30/40)|round(2) = 75.0 -> "75.0"; legend pct = round -> "75.0"
        plat = next(s for s in commits["bars"] if s["id"] == "platform")
        self.assertEqual(plat["width"], "75.0")
        self.assertEqual(plat["tip"], "Platform: 30")     # bar tip ALWAYS uses num
        plat_leg = next(s for s in commits["legend"] if s["id"] == "platform")
        self.assertEqual(plat_leg["pct"], "75.0")
        self.assertEqual(plat_leg["value"], "30")   # commits count via num

    def test_loc_bar_value_uses_loc_filter(self):
        out = render.repositories_json({"split": _split()}, _meta())
        loc_bar = out["split"]["bars"][2]
        plat = next(s for s in loc_bar["legend"] if s["id"] == "platform")
        self.assertEqual(plat["value"], "3.0K")           # 3000 via _loc -> "3.0K"
        # the LOC bar's tip still uses num (typebar macro: t[field]|num)
        plat_bar = next(s for s in loc_bar["bars"] if s["id"] == "platform")
        self.assertEqual(plat_bar["tip"], "Platform: 3,000")

    def test_zero_field_dropped_from_bar_kept_conditionally_in_legend(self):
        s = _split(types=[
            {"id": "platform", "name": "Platform", "color": "#5b5bf0", "commits": 40, "prs": 0, "loc": 4000},
        ], prs_total=0)
        out = render.repositories_json({"split": s}, _meta())
        prs_bar = out["split"]["bars"][1]
        self.assertEqual(prs_bar["bars"], [])       # prs=0 -> no segment
        self.assertEqual(prs_bar["legend"], [])     # prs=0 -> no legend entry


class ReposJsonEnvelopeTest(unittest.TestCase):
    def test_full_payload_shape(self):
        meta = _meta(org="acme-org", all_time=True, window_start="2008-01-01",
                     lookback_days=100, generated="2026-07-11T16:10:05Z",
                     all_label="All-time", scope_targets={"org": ["acme-org"]},
                     data_quality={"api_rate_limited": False, "api_reset": None},
                     window_labels=["30d", "all"])
        out = render.repositories_json({"split": _split()}, meta)
        self.assertTrue(out["ok"])
        self.assertEqual(out["meta"]["org"], "acme-org")
        self.assertEqual(out["meta"]["generatedText"], "2026-07-11 16:10")
        self.assertEqual(out["period"], {"preset": "all", "label": "All-time", "from": None, "to": None})
        self.assertEqual(out["periodPresets"], [{"key": "30d", "label": "30 days"},
                                                {"key": "all", "label": "All-time"}])
        self.assertEqual(out["scopeTargets"], {"org": ["acme-org"]})


class RepositoriesApiEndpointTest(unittest.TestCase):
    """Same ThreadingHTTPServer + isolated REPORT_DB harness as
    PeopleApiEndpointTest (tests/test_people_api.py) — the validation paths
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
        status, body = self._get("/api/report/repositories")
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "no collected data")

    def test_invalid_scope_returns_400(self):
        status, body = self._get("/api/report/repositories?slice=notalevel:x")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid scope")

    def test_invalid_dates_return_400(self):
        status, body = self._get("/api/report/repositories?from=not-a-date")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_invalid_preset_returns_400(self):
        status, body = self._get("/api/report/repositories?p=nonsense")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid p")

    def test_from_after_to_returns_400(self):
        status, body = self._get("/api/report/repositories?from=2026-01-10&to=2026-01-01")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "from is after to")


if __name__ == "__main__":
    unittest.main()
