"""Tests for the /flow React route (see
docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P4):

- render.flow_json(): a pure function fed a hand-built `pr` dict shaped like
  serve_flow's own `{"flow": semantic_metrics.flow_report(...)}` — same
  convention as tests/test_delivery_api.py's DeliveryJsonTest.
- GET /api/report/flow: the same ThreadingHTTPServer + isolated REPORT_DB
  harness as DeliveryApiEndpointTest — validation paths that don't need real
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


def _flow(**over):
    f = {
        "has_data": True, "n_items": 20, "n_prs": 12, "n_issues": 8,
        "reopen_rate": 15.0, "reopened_n": 3,
        "bounce_rate": 8.3, "bounced_n": 1,
        "rereq_rate": 25.0, "rereq_n": 5,
        "cr_rate": 33.3, "cr_prs": 4, "cr_rounds": 6,
        "cycle": {
            "ttfr": {"h": 4.5, "n": 10},
            "review_to_merge": {"h": 20.0, "n": 9},
            "ttm": {"h": 30.0, "n": 9},
            "draft_to_ready": {"h": None, "n": 0},
            "ttc": {"h": 12.0, "n": 6},
        },
        "min_items": 3,
        "people": [
            {"login": "alice", "name": "Alice A", "items": 5, "friction": 0.4,
             "reopen_pct": 20, "bounce_pct": 0, "extra_reqs": 1,
             "cr_rounds": 2, "cr_prs": 1, "ttm_med": 18.0, "ttfr_med": 3.0},
            {"login": "bob", "name": "Bob B", "items": 4, "friction": None,
             "reopen_pct": 0, "bounce_pct": 25, "extra_reqs": 0,
             "cr_rounds": 0, "cr_prs": 0, "ttm_med": None, "ttfr_med": None},
        ],
        "cfd": {
            "has_data": True, "n_dates": 5, "first_date": "2026-01-01", "last_date": "2026-01-05",
            "dates": ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"],
            "series": [
                {"company": "Done", "name": "Done", "key": "done", "color": "#10b981",
                 "vals": [1, 2, 3, 4, 5]},
                {"company": "In progress", "name": "In progress", "key": "in_progress",
                 "color": "#8b5cf6", "vals": [2, 2, 1, 1, 0]},
            ],
        },
        "dwell": {
            "has_data": True, "dwell_median_h": 10.0, "dwell_n": 8,
            "age_median_h": 5.0, "age_n": 4, "age_max_h": 40.0,
            "first_date": "2026-01-01", "n_dates": 5,
            "stages": [
                {"key": "in_progress", "name": "In progress", "color": "#8b5cf6",
                 "median_h": 8.0, "avg_h": 9.0, "n": 6, "age_median_h": 5.0, "n_current": 4},
            ],
        },
        "rewinds": {
            "has_history": True, "n_dates": 5, "first_date": "2026-01-01", "last_date": "2026-01-05",
            "qa_to_dev": 2, "events": [{"repo": "x"}], "by_person": {"alice": 2},
        },
    }
    f.update(over)
    return f


def _meta(**over):
    meta = {
        "org": "acme-org", "all_time": True, "window_start": "2008-01-01", "lookback_days": 100,
        "generated": "2026-07-23T09:00:00Z", "all_label": "All-time",
        "scope_targets": {"org": ["acme-org"]}, "window_labels": ["30d", "all"],
    }
    meta.update(over)
    return meta


class FlowJsonTest(unittest.TestCase):
    def test_envelope_shape_matches_overview_trend_delivery_convention(self):
        out = render.flow_json({"flow": _flow()}, _meta())
        self.assertTrue(out["ok"])
        self.assertEqual(out["meta"]["org"], "acme-org")
        self.assertEqual(out["meta"]["generatedText"], "2026-07-23 09:00")
        self.assertEqual(out["period"], {"preset": "all", "label": "All-time", "from": None, "to": None})
        self.assertEqual(out["periodPresets"], [{"key": "30d", "label": "30 days"},
                                                 {"key": "all", "label": "All-time"}])
        self.assertEqual(out["scopeTargets"], {"org": ["acme-org"]})

    def test_no_cohort_data_degrades_the_whole_view(self):
        # templates/panels/04_flow.j2 gates EVERYTHING on f.has_data, including
        # the board-movement views (cfd/dwell/rewinds), which are computed
        # independently of the cohort — this is a deliberate quirk to mirror,
        # not a bug to fix (see render.flow_json's docstring).
        out = render.flow_json({"flow": {"has_data": False,
                                          "cfd": {"has_data": True}, "dwell": {}, "rewinds": {}}}, _meta())
        self.assertEqual(out["flow"], {"hasData": False})

    def test_degrades_gracefully_on_empty_input(self):
        out = render.flow_json({}, _meta())
        self.assertEqual(out["flow"], {"hasData": False})

    def test_health_rates_pass_through_as_raw_numbers(self):
        # Percentages travel as raw numbers (fmtPct has an exact TS twin —
        # see lib/format.ts), not pre-formatted strings, matching the
        # convention Delivery/Trend already use for non-tile values.
        out = render.flow_json({"flow": _flow()}, _meta())
        health = out["flow"]["health"]
        self.assertEqual(health["crRate"], 33.3)
        self.assertEqual(health["reopenRate"], 15.0)
        self.assertEqual(health["bounceRate"], 8.3)
        self.assertEqual(health["rereqRate"], 25.0)
        self.assertEqual(health["crPrs"], 4)
        self.assertEqual(health["crRounds"], 6)

    def test_cycle_skips_segments_with_no_data(self):
        out = render.flow_json({"flow": _flow()}, _meta())
        cycle = out["flow"]["cycle"]
        # draft_to_ready has h=None in the fixture -> dropped, matching the
        # template's `{% if c.h is not none %}` guard
        self.assertEqual([c["key"] for c in cycle], ["ttfr", "review_to_merge", "ttm", "ttc"])
        ttfr = cycle[0]
        self.assertEqual(ttfr["h"], "4.5h")     # pre-formatted via render._hours (no TS twin)
        self.assertEqual(ttfr["n"], 10)
        self.assertEqual(ttfr["label"], "Open → first review")

    def test_people_friction_formatted_as_string_with_colour(self):
        out = render.flow_json({"flow": _flow()}, _meta())
        alice, bob = out["flow"]["people"]
        self.assertEqual(alice["login"], "alice")
        self.assertEqual(alice["friction"], "0.4")     # str(round(x,2)) — matches Jinja's raw {{ }}
        self.assertEqual(alice["frictionColor"], "#10b981")   # < 0.5 -> green
        self.assertEqual(alice["ttmMed"], "18h")
        self.assertEqual(alice["ttfrMed"], "3h")
        # bob has no friction score -> None/None, matching the template's "—" branch
        self.assertIsNone(bob["friction"])
        self.assertIsNone(bob["frictionColor"])
        self.assertIsNone(bob["ttmMed"])

    def test_friction_colour_thresholds(self):
        out = render.flow_json({"flow": _flow(people=[
            {"login": "a", "name": "a", "items": 3, "friction": 0.4, "reopen_pct": 0, "bounce_pct": 0,
             "extra_reqs": 0, "cr_rounds": 0, "cr_prs": 0, "ttm_med": None, "ttfr_med": None},
            {"login": "b", "name": "b", "items": 3, "friction": 1.0, "reopen_pct": 0, "bounce_pct": 0,
             "extra_reqs": 0, "cr_rounds": 0, "cr_prs": 0, "ttm_med": None, "ttfr_med": None},
            {"login": "c", "name": "c", "items": 3, "friction": 2.0, "reopen_pct": 0, "bounce_pct": 0,
             "extra_reqs": 0, "cr_rounds": 0, "cr_prs": 0, "ttm_med": None, "ttfr_med": None},
        ])}, _meta())
        colors = [p["frictionColor"] for p in out["flow"]["people"]]
        self.assertEqual(colors, ["#10b981", "#f59e0b", "#ef4444"])

    def test_cfd_spec_built_when_has_data(self):
        out = render.flow_json({"flow": _flow()}, _meta())
        cfd = out["flow"]["cfd"]
        self.assertTrue(cfd["hasData"])
        self.assertEqual(cfd["nDates"], 5)
        self.assertEqual(cfd["firstDate"], "2026-01-01")
        self.assertEqual([s["key"] for s in cfd["series"]], ["done", "in_progress"])
        self.assertIsNotNone(cfd["spec"])

    def test_cfd_no_data(self):
        out = render.flow_json({"flow": _flow(cfd={"has_data": False, "n_dates": 1,
                                                     "first_date": "2026-01-01"})}, _meta())
        cfd = out["flow"]["cfd"]
        self.assertFalse(cfd["hasData"])
        self.assertIsNone(cfd["spec"])
        self.assertEqual(cfd["series"], [])

    def test_dwell_stages_and_hours_formatted(self):
        out = render.flow_json({"flow": _flow()}, _meta())
        dwell = out["flow"]["dwell"]
        self.assertTrue(dwell["hasData"])
        self.assertEqual(dwell["ageMedianH"], "5h")
        self.assertEqual(dwell["dwellMedianH"], "10h")
        stage = dwell["stages"][0]
        self.assertEqual(stage["nCurrent"], 4)
        self.assertEqual(stage["medianH"], "8h")

    def test_durations_travel_as_a_formatted_string_and_a_raw_number(self):
        """Every duration the Flow tables show is pre-formatted server-side ("18h",
        "5d") because render._hours has no TypeScript twin. A DataTable column emits
        that string as the cell AND the raw value as data-sort, so both have to be in
        the payload: sorting "38.8h" against "3h" as text puts the slow row second.
        The pairing is easy to half-do when a column is added, hence one test over all
        of them rather than an assertion buried in each table's own test."""
        out = render.flow_json({"flow": _flow()}, _meta())["flow"]
        alice, bob = out["people"]
        self.assertEqual((alice["ttmMed"], alice["ttmMedHours"]), ("18h", 18.0))
        self.assertEqual((alice["ttfrMed"], alice["ttfrMedHours"]), ("3h", 3.0))
        # a missing median is None on BOTH halves — never "—" on one and 0 on the other,
        # which would sort an unmeasurable row alongside the fastest ones
        self.assertIsNone(bob["ttmMed"])
        self.assertIsNone(bob["ttmMedHours"])
        self.assertIsNone(bob["ttfrMed"])
        self.assertIsNone(bob["ttfrMedHours"])
        stage = out["dwell"]["stages"][0]
        self.assertEqual((stage["ageMedianH"], stage["ageMedianHours"]), ("5h", 5.0))
        self.assertEqual((stage["medianH"], stage["medianHours"]), ("8h", 8.0))

    def test_rewinds_counts(self):
        out = render.flow_json({"flow": _flow()}, _meta())
        rewinds = out["flow"]["rewinds"]
        self.assertTrue(rewinds["hasHistory"])
        self.assertTrue(rewinds["hasEvents"])
        self.assertEqual(rewinds["qaToDev"], 2)
        self.assertEqual(rewinds["ownerCount"], 1)

    def test_rewinds_no_events(self):
        out = render.flow_json({"flow": _flow(rewinds={"has_history": True, "n_dates": 5,
                                                         "first_date": "2026-01-01", "qa_to_dev": 0,
                                                         "events": [], "by_person": {}})}, _meta())
        rewinds = out["flow"]["rewinds"]
        self.assertTrue(rewinds["hasHistory"])
        self.assertFalse(rewinds["hasEvents"])


class FlowApiEndpointTest(unittest.TestCase):
    """Same ThreadingHTTPServer + isolated REPORT_DB harness as
    DeliveryApiEndpointTest — the validation paths below need no real
    collected data."""

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
        status, body = self._get("/api/report/flow")
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "no collected data")

    def test_invalid_scope_returns_400(self):
        status, body = self._get("/api/report/flow?slice=notalevel:x")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid scope")

    def test_invalid_dates_return_400(self):
        status, body = self._get("/api/report/flow?from=not-a-date")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_invalid_preset_returns_400(self):
        status, body = self._get("/api/report/flow?p=nonsense")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid p")

    def test_from_after_to_returns_400(self):
        status, body = self._get("/api/report/flow?from=2026-01-10&to=2026-01-01")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "from is after to")


if __name__ == "__main__":
    unittest.main()
