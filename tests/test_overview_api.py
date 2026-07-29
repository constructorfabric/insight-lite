"""Tests for the /overview React route's scaffold (see
docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P1):

- render.overview_json() and its per-section helpers (_kpi_tiles_json,
  _delta_chip, _contributors_json, _weekly_json, _worktype_json, _score_json):
  pure functions fed hand-built dicts shaped like store.aggregate()'s return
  (same convention as serve_custom_period/build_model's all_block), so the
  JSON-shaping logic is covered without needing a fully-seeded store.
- GET /api/report/overview: the same ThreadingHTTPServer + isolated REPORT_DB
  harness as WhatsNewApiEndpointTest (tests/test_whats_new.py) / VegaAssetEndpointTest
  (tests/test_dashboards.py) — validation paths (no data / bad params) that
  don't need real collected data, mirroring PortalSecurityTest's
  test_period_without_db_returns_404 for /api/period.
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


class KpiTilesJsonTest(unittest.TestCase):
    def _pr(self, **over):
        pr = {
            "totals": {"commits": 5848, "meaningful_additions": 1234567, "prs": 120,
                       "prs_merged": 90, "specs": 30, "bugs": 4, "features": 12, "people": 15},
            "loc_added_h": "1,234,567",
            "spark": {"commits_pts": "0,0 100,0", "loc_pts": "0,0 100,0", "prs_pts": None,
                      "specs_pts": None, "bugs_pts": None, "epics_pts": None, "features_pts": None,
                      "people_pts": None},
        }
        pr.update(over)
        return pr

    def test_eight_tiles_in_order(self):
        tiles = render._kpi_tiles_json(self._pr())
        self.assertEqual([t["label"] for t in tiles],
                          ["commits", "meaningful LOC", "PRs opened", "spec edits", "bugs opened",
                           "epics opened", "features opened", "active people"])

    def test_values_use_the_same_number_formatters_as_the_monolith(self):
        tiles = render._kpi_tiles_json(self._pr())
        self.assertEqual(tiles[0]["value"], "5,848")           # _num
        self.assertEqual(tiles[1]["value"], "1.23M")           # _loc
        self.assertEqual(tiles[1]["sub"], "1,234,567 · code volume")
        self.assertEqual(tiles[1]["tip"], "1,234,567 meaningful lines added")
        self.assertEqual(tiles[2]["sub"], "90 merged")

    def test_commits_sub_switches_on_deltas_presence(self):
        no_delta = render._kpi_tiles_json(self._pr())
        self.assertEqual(no_delta[0]["sub"], "in period")
        with_delta = render._kpi_tiles_json(self._pr(deltas={"commits": {"diff": 1, "prev": 0, "pct": None, "dir": "up"}}))
        self.assertEqual(with_delta[0]["sub"], "vs prev period")

    def test_people_tile_drill_omitted_when_zero(self):
        tiles = render._kpi_tiles_json(self._pr(totals={**self._pr()["totals"], "people": 0}))
        self.assertIsNone(tiles[-1]["drill"])
        tiles2 = render._kpi_tiles_json(self._pr())
        self.assertEqual(tiles2[-1]["drill"], {"drill": "people"})


class DeltaChipTest(unittest.TestCase):
    def test_none_when_no_delta(self):
        self.assertIsNone(render._delta_chip(None))
        self.assertIsNone(render._delta_chip({}))

    def test_up_and_down_and_flat(self):
        up = render._delta_chip({"diff": 10, "prev": 100, "pct": 10, "dir": "up"})
        self.assertEqual(up, {"cls": "up", "text": "▲ 10%", "tip": "vs previous equal period (was 100)"})
        down = render._delta_chip({"diff": -5, "prev": 20, "pct": -25, "dir": "down"})
        self.assertEqual(down["cls"], "down")
        self.assertEqual(down["text"], "▼ 25%")
        flat = render._delta_chip({"diff": 0, "prev": 10, "pct": 0, "dir": "flat"})
        self.assertEqual(flat["cls"], "flat")
        self.assertEqual(flat["text"], "± 0%")

    def test_a_change_that_rounds_to_zero_does_not_keep_its_arrow(self):
        """"▲ 0%" is an arrow arguing with its own number. A sub-1% move is flat to a
        reader, so it says so and puts the direction in the tooltip instead. Surfaced
        by the Flow rates, where a fraction of a percent is common."""
        chip = render._delta_chip({"diff": 0.2, "prev": 50.1, "pct": 0, "dir": "up"})
        self.assertEqual(chip["cls"], "flat")
        self.assertEqual(chip["text"], "± <1%")
        self.assertIn("up by under 1%", chip["tip"])
        # and the same for a tiny fall, including when 'up is bad' flips the colours
        low = render._delta_chip({"diff": -0.2, "prev": 50.1, "pct": 0, "dir": "down"},
                                 lower_better=True)
        self.assertEqual(low["cls"], "flat")
        self.assertIn("down by under 1%", low["tip"])

    def test_new_when_prev_was_zero(self):
        chip = render._delta_chip({"diff": 5, "prev": 0, "pct": None, "dir": "up"})
        self.assertEqual(chip, {"cls": "up", "text": "▲ new", "tip": "nothing in the previous equal period"})


class ContributorsJsonTest(unittest.TestCase):
    def test_none_when_no_block(self):
        self.assertIsNone(render._contributors_json(None))
        self.assertIsNone(render._contributors_json({}))

    def test_tiles_carry_color_and_signed_delta(self):
        block = {
            "tiles": [{"label": "Total contributors", "now": 118, "delta": 29, "color": "#1f2328"},
                      {"label": "Example Inc", "now": 46, "delta": -3, "color": "#8250df"}],
            "series": [{"name": "Total", "color": "#1f2328", "vals": [1, 2, 3]}],
            "dates": ["2026-01", "2026-02", "today"], "since": "2026-04-01", "points": 3,
        }
        out = render._contributors_json(block)
        self.assertEqual(out["tiles"][0], {"value": "118", "label": "Total contributors",
                                            "color": "#1f2328", "sub": "+29 in 90d"})
        self.assertEqual(out["tiles"][1]["sub"], "-3 in 90d")
        self.assertIsNotNone(out["chartSpec"])
        self.assertEqual(out["legend"], [{"name": "Total", "color": "#1f2328"}])
        self.assertEqual(out["since"], "2026-04-01")


class WorktypeJsonTest(unittest.TestCase):
    def test_rows_and_total(self):
        pr = {"commit_types": [{"type": "fix", "count": 10, "pct": 40.0},
                                {"type": "feat", "count": 15, "pct": 60.0}]}
        out = render._worktype_json(pr)
        self.assertEqual(out["total"], 25)
        self.assertEqual([r["type"] for r in out["rows"]], ["fix", "feat"])
        self.assertTrue(out["rows"][0]["color"].startswith("#"))
        self.assertIsNone(out["breakdown"])

    def test_breakdown_present_when_worktype_break_has_rows(self):
        pr = {"commit_types": [{"type": "fix", "count": 1, "pct": 100.0}],
              "worktype_break": {"type_cols": ["fix"],
                                  "by_company": [{"company": "Acme", "types": {"fix": 1}, "total": 1}],
                                  "by_repo": []}}
        out = render._worktype_json(pr)
        self.assertIsNotNone(out["breakdown"])
        self.assertEqual(out["breakdown"]["typeCols"], ["fix"])
        self.assertEqual(len(out["breakdown"]["byCompany"]), 1)


class ScoreJsonTest(unittest.TestCase):
    def test_none_when_nobody_scored(self):
        self.assertIsNone(render._score_json(None))
        self.assertIsNone(render._score_json({"n": 0}))

    def test_shape_and_pillar_filtering(self):
        score = {
            "n": 2, "median": 50, "active_pillars": ["engagement", "delivery"],
            "bands": [{"band": "Strong", "tone": "good", "n": 1}, {"band": "Building", "tone": "weak", "n": 1}],
            "top": [{"rank": 1, "login": "alice", "name": "Alice", "score": 80,
                     "contributions": {"engagement": 40, "delivery": 40, "craft": 0, "flow": None}}],
            "by_company": [{"company": "Acme", "n": 2, "median": 50, "mean": 50}],
            "team_medians": {"commits": 12, "ttm": 3.25, "rounds": 2.0, "flow": None},
            "weights": {},
        }
        out = render._score_json(score)
        self.assertEqual(out["n"], 2)
        self.assertEqual(out["bands"][0], {"band": "Strong", "n": 1, "color": "#10b981"})
        # craft/flow excluded: not in active_pillars, and flow is None anyway
        self.assertEqual(out["top"][0]["contributions"], {"engagement": 40, "delivery": 40})
        self.assertEqual(out["teamMedians"]["commits"], "12")
        self.assertEqual(out["teamMedians"]["ttm"], "3.2h")
        self.assertIsNone(out["teamMedians"]["rounds"])   # 'craft' not active
        self.assertIsNone(out["teamMedians"]["flow"])     # 'flow' not active


class OverviewJsonTest(unittest.TestCase):
    def test_full_payload_shape(self):
        pr = {"totals": {"commits": 1, "meaningful_additions": 1, "prs": 1, "prs_merged": 1,
                         "specs": 0, "bugs": 0, "features": 0, "people": 1},
              "loc_added_h": "1", "spark": {}, "company_rows": [{"company": "Acme"}],
              "weekly": None, "commit_types": [], "score": None}
        meta = {"org": "acme-org", "all_time": True, "window_start": "2008-01-01",
                "lookback_days": 100, "generated": "2026-07-11T16:10:05Z",
                "all_label": "All-time", "contrib_block": None, "scope_targets": {"org": ["acme-org"]},
                "data_quality": {"api_rate_limited": False, "api_reset": None},
                "window_labels": ["30d", "all"]}
        out = render.overview_json(pr, meta)
        self.assertTrue(out["ok"])
        self.assertEqual(out["meta"]["org"], "acme-org")
        self.assertEqual(out["meta"]["generatedText"], "2026-07-11 16:10")
        self.assertEqual(out["period"], {"preset": "all", "label": "All-time", "from": None, "to": None})
        self.assertEqual(out["periodPresets"], [{"key": "30d", "label": "30 days"},
                                                 {"key": "all", "label": "All-time"}])
        self.assertEqual(out["scopeTargets"], {"org": ["acme-org"]})
        self.assertIsNone(out["contributors"])
        self.assertEqual(out["companies"]["rows"], [{"company": "Acme"}])
        self.assertIsNone(out["weekly"])
        self.assertIsNone(out["score"])
        self.assertEqual(len(out["kpis"]), 8)


class OverviewApiEndpointTest(unittest.TestCase):
    """Same ThreadingHTTPServer + isolated REPORT_DB harness as
    WhatsNewApiEndpointTest (tests/test_whats_new.py) — the validation paths
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
        status, body = self._get("/api/report/overview")
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "no collected data")

    def test_invalid_scope_returns_400(self):
        status, body = self._get("/api/report/overview?slice=notalevel:x")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid scope")

    def test_invalid_dates_return_400(self):
        status, body = self._get("/api/report/overview?from=not-a-date")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_invalid_preset_returns_400(self):
        status, body = self._get("/api/report/overview?p=nonsense")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid p")

    def test_from_after_to_returns_400(self):
        status, body = self._get("/api/report/overview?from=2026-01-10&to=2026-01-01")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "from is after to")


if __name__ == "__main__":
    unittest.main()
