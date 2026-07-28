"""Tests for the /people React route (see
docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P5):

- render.people_json(): a pure function fed a hand-built `pr` dict shaped
  like store.aggregate()'s return (+ optional 'score'/'deltas') — same
  convention as tests/test_overview_api.py's OverviewJsonTest. People has no
  separate collector: `pr['people']`/`pr['categories']`/`pr['reviews']`/
  `pr['split']` are the SAME fields Overview's `pr` carries.
- GET /api/report/people: the same ThreadingHTTPServer + isolated REPORT_DB
  harness as OverviewApiEndpointTest/DeliveryApiEndpointTest — validation
  paths that don't need real collected data.
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


def _person(login, **over):
    p = {
        "login": login, "name": login.title(), "company": "Acme", "is_member": True,
        "klass": "member", "commits": 10, "loc": 500, "raw_loc": 600, "prs": 3,
        "merged_prs": 2, "specs": 1, "bugs": 1, "features": 2, "epics": 0,
        "by_type": {"platform": 8, "app": 2}, "ai_commits": 2,
        "reviews": 4, "approvals": 3, "ttm": 4.0, "cpt_lines": 100,
        "surv_code_human": 200, "surv_code_ai": 50, "surv_spec": 10,
        "surv_win_code": None, "code_commits": 9, "mix_specs_pct": 10.0, "mix_code_pct": 90.0,
    }
    p.update(over)
    return p


def _pr(**over):
    pr = {
        "people": [_person("alice", commits=20, loc=1000), _person("bob", commits=10, loc=500)],
        "split": {"types": [{"id": "platform", "name": "Platform", "color": "#5b5bf0"},
                             {"id": "app", "name": "Apps", "color": "#10b981"}]},
        "categories": [
            {"key": "code", "title": "Code", "unit": "commits + PRs", "total": 30,
             "rows": [{"login": "alice", "value": 20, "pct": 66.7}, {"login": "bob", "value": 10, "pct": 33.3}],
             "top3": 100.0, "n80": 1, "tail_n": 0, "tail_pct": 0.0, "tail_value": 0},
            {"key": "specs", "title": "Specs", "unit": "commits to spec docs", "total": 2,
             "rows": [{"login": "alice", "value": 1, "pct": 50.0}, {"login": "bob", "value": 1, "pct": 50.0}],
             "top3": 100.0, "n80": 2, "tail_n": 0, "tail_pct": 0.0, "tail_value": 0},
        ],
        "reviews": {},
    }
    pr.update(over)
    return pr


class PeopleJsonCategoriesTest(unittest.TestCase):
    def test_code_category_has_no_drill(self):
        out = render.people_json(_pr(), {})
        code = next(c for c in out["categories"] if c["key"] == "code")
        self.assertIsNone(code["drillKind"])
        self.assertIsNone(code["drillFlag"])
        self.assertFalse(code["valueIsLoc"])

    def test_specs_category_has_drill_with_flag(self):
        out = render.people_json(_pr(), {})
        specs = next(c for c in out["categories"] if c["key"] == "specs")
        self.assertEqual(specs["drillKind"], "commit")
        self.assertEqual(specs["drillFlag"], "is_spec")

    def test_code_loc_category_is_loc_formatted(self):
        pr = _pr(categories=[{"key": "code_loc", "title": "Code (LOC)", "unit": "meaningful LOC added",
                              "total": 1500, "rows": [], "top3": 0, "n80": 0, "tail_n": 0,
                              "tail_pct": 0, "tail_value": 0}])
        out = render.people_json(pr, {})
        cl = out["categories"][0]
        self.assertTrue(cl["valueIsLoc"])
        self.assertEqual(cl["drillKind"], "commit")
        self.assertEqual(cl["drillFlag"], None)   # empty-string flag -> None

    def test_row_email_from_meta(self):
        out = render.people_json(_pr(), {"emails_by_login": {"alice": "alice@acme.com"}})
        code = next(c for c in out["categories"] if c["key"] == "code")
        self.assertEqual(code["rows"][0]["email"], "alice@acme.com")
        self.assertEqual(code["rows"][1]["email"], "")


class PeopleJsonReviewsTest(unittest.TestCase):
    def test_none_when_no_prs_reviewed(self):
        out = render.people_json(_pr(reviews={"total_prs": 0}), {})
        self.assertIsNone(out["reviews"])

    def test_shape_and_preformatted_medians(self):
        pr = _pr(reviews={
            "total_prs": 10, "reviewed_prs": 8, "coverage_pct": 80.0,
            "median_ttm_h": 4.0, "merged": 7, "windowed": True,
            "reviewers": [{"login": "alice", "reviews": 5, "approvals": 4, "latency_h": 2.0},
                          {"login": "bob", "reviews": 0, "approvals": 0, "latency_h": None}],
        })
        out = render.people_json(pr, {})
        rv = out["reviews"]
        self.assertEqual(rv["totalPrs"], 10)
        self.assertEqual(rv["medianTtmH"], "4.0")     # pre-formatted STRING, not 4.0 float
        self.assertEqual(rv["reviewers"][0]["latencyH"], "2.0")
        self.assertIsNone(rv["reviewers"][1]["latencyH"])
        self.assertEqual(rv["reviewers"][0]["barPct"], 100.0)   # 5 / max(5) * 100

    def test_review_load_breakdown_only_when_present(self):
        pr = _pr(reviews={"total_prs": 1, "reviewers": []})
        out = render.people_json(pr, {})
        self.assertNotIn("byCompany", out["reviews"])
        self.assertNotIn("byRepo", out["reviews"])

        meta = {
            "reviews_by_company": [{"company": "Acme", "reviews": 3, "approvals": 2,
                                    "review_latency_h": 1.0, "median_ttm_h": 5.0, "merged": 2}],
            "reviews_by_repo": [{"repo": "example-core", "total": 4, "reviewed": 3,
                                 "coverage_pct": 75.0, "median_ttm_h": 3.0}],
            "legacy_names": ["example-core"],
        }
        out2 = render.people_json(pr, meta)
        self.assertEqual(out2["reviews"]["byCompany"][0]["reviewLatencyH"], "1.0")
        self.assertEqual(out2["reviews"]["byCompany"][0]["medianTtmH"], "5.0")
        self.assertEqual(out2["reviews"]["byRepo"][0]["legacy"], True)
        self.assertEqual(out2["reviews"]["byRepo"][0]["medianTtmH"], "3.0")


class PeopleJsonPeopleTest(unittest.TestCase):
    def test_person_rows_precompute(self):
        out = render.people_json(_pr(), {"emails_by_login": {"alice": "alice@acme.com"}})
        rows = out["people"]["rows"]
        alice = next(r for r in rows if r["login"] == "alice")
        self.assertEqual(alice["email"], "alice@acme.com")
        self.assertFalse(alice["not_member"])
        self.assertEqual(alice["commits_pct"], 100.0)    # 20 / max(20,10) * 100
        self.assertEqual(alice["loc_pct"], 100.0)
        self.assertEqual(alice["ttm"], "4.0")            # pre-formatted string
        bob = next(r for r in rows if r["login"] == "bob")
        self.assertEqual(bob["commits_pct"], 50.0)       # 10 / 20 * 100

    def test_ttm_none_stays_none(self):
        pr = _pr(people=[_person("carol", ttm=None)])
        out = render.people_json(pr, {})
        self.assertIsNone(out["people"]["rows"][0]["ttm"])

    def test_split_types_and_cap_passthrough(self):
        out = render.people_json(_pr(), {})
        self.assertEqual(out["people"]["splitTypes"],
                          [{"id": "platform", "name": "Platform", "color": "#5b5bf0"},
                           {"id": "app", "name": "Apps", "color": "#10b981"}])
        self.assertEqual(out["people"]["cap"], 40)

    def test_ranked_by_label_switches_on_pr_label(self):
        out_all = render.people_json(_pr(label="all"), {})
        self.assertEqual(out_all["people"]["rankedByLabel"], "surviving hand-written code")
        out_custom = render.people_json(_pr(label="custom"), {})
        self.assertEqual(out_custom["people"]["rankedByLabel"], "period activity")


class PeopleJsonEnvelopeTest(unittest.TestCase):
    def test_full_payload_shape(self):
        meta = {"org": "acme-org", "all_time": True, "window_start": "2008-01-01",
                "lookback_days": 100, "generated": "2026-07-11T16:10:05Z",
                "all_label": "All-time", "scope_targets": {"org": ["acme-org"]},
                "data_quality": {"api_rate_limited": False, "api_reset": None},
                "window_labels": ["30d", "all"]}
        out = render.people_json(_pr(), meta)
        self.assertTrue(out["ok"])
        self.assertEqual(out["meta"]["org"], "acme-org")
        self.assertEqual(out["meta"]["generatedText"], "2026-07-11 16:10")
        self.assertEqual(out["period"], {"preset": "all", "label": "All-time", "from": None, "to": None})
        self.assertEqual(out["periodPresets"], [{"key": "30d", "label": "30 days"},
                                                 {"key": "all", "label": "All-time"}])
        self.assertEqual(out["scopeTargets"], {"org": ["acme-org"]})
        self.assertEqual(len(out["categories"]), 2)
        self.assertEqual(len(out["people"]["rows"]), 2)


class PeopleApiEndpointTest(unittest.TestCase):
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
        status, body = self._get("/api/report/people")
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "no collected data")

    def test_invalid_scope_returns_400(self):
        status, body = self._get("/api/report/people?slice=notalevel:x")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid scope")

    def test_invalid_dates_return_400(self):
        status, body = self._get("/api/report/people?from=not-a-date")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_invalid_preset_returns_400(self):
        status, body = self._get("/api/report/people?p=nonsense")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid p")

    def test_from_after_to_returns_400(self):
        status, body = self._get("/api/report/people?from=2026-01-10&to=2026-01-01")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "from is after to")


if __name__ == "__main__":
    unittest.main()
