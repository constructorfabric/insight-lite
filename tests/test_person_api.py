"""Tests for the /person React route (see
docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P6):

- render.person_json(): a pure function fed a hand-built `dash` payload shaped
  like server.py's serve_person builds ({profile, alltime, weekly, heat, emails,
  login, gh_profile, score}) — same convention as tests/test_people_api.py's
  PeopleJson* tests. `None` dash is the no-person (picker-only) state.
- GET /api/report/person: exercised against the repo's real history/report.db
  (REPORT_DB) with and without a `person=` — a couple of shape + value asserts —
  plus the validation paths (invalid person / no data) on an isolated temp DB,
  the same ThreadingHTTPServer harness as PeopleApiEndpointTest.
"""
import json
import os
import sqlite3
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import render

REAL_DB = Path(__file__).resolve().parent.parent / "history" / "report.db"


def _dash(**over):
    """A serve_person-shaped payload for one person with all-time footprint."""
    d = {
        "login": "alice",
        "profile": {
            "login": "alice",
            "totals": {"commits": 20, "meaningful_additions": 1500, "specs": 2, "ai_commits": 3,
                       "prs": 5, "prs_merged": 4, "issues": 6, "bugs": 1, "features": 2, "epics": 1},
            "shares": {"commits": 25.0, "meaningful_additions": 30.0, "specs": 10.0, "prs": 20.0},
            "deltas": {}, "spark": {"commits_pts": "0,26 100,0", "loc_pts": "0,26 100,0"},
            "rank": 2, "n_people": 40,
            "repos": [{"repo": "acme/gears-core", "name": "gears-core", "commits": 12, "add": 900, "del": 100}],
            "split": {"types": [{"id": "platform", "name": "Platform", "color": "#5b5bf0", "commits": 15},
                                 {"id": "app", "name": "Apps", "color": "#10b981", "commits": 5}], "total": 20},
            "elements": [{"element": "Insight", "commits": 8, "loc": 700}],
            "work_type": [{"type": "feat", "count": 10}, {"type": "fix", "count": 6}],
            "mix": {"code": 18, "specs": 2, "pct_code": 90.0, "pct_specs": 10.0},
        },
        "alltime": {"name": "Alice A", "company": "Acme", "is_member": True,
                    "identity_confidence": "high", "identity_evidence": "email match",
                    "surv_code_human": 2000, "surv_code_ai": 500, "surv_spec": 120,
                    "reviews": 40, "approvals": 30, "merged_prs": 25, "prs": 30,
                    "ttm": 4.0, "ai_commits": 12, "cpt_lines": 800, "commits": 1234},
        "heat": [{"week": "2026-01-05", "commits": 5, "issues": 1},
                 {"week": "2026-01-12", "commits": 8, "issues": 0}],
        "weekly": {"login": "alice", "columns": [{"repo": "acme/gears-core", "name": "gears-core"}],
                   "rows": [{"week": "2026-01-05", "week_end": "2026-01-11",
                             "cells": [{"commits": 5, "add": 400, "del": 50}], "issues": 1}],
                   "col_totals": [{"commits": 5, "add": 400, "del": 50}],
                   "grand": {"commits": 5, "add": 400, "del": 50, "issues": 1},
                   "since": "2026-01-05", "until": "2026-01-12"},
        "emails": "alice@acme.com",
        "gh_profile": {"name": "Alice", "company": "@acme", "location": "Berlin", "bio": "builds things"},
        "score": None,
    }
    d.update(over)
    return d


_META = {"org": "acme-org", "all_time": True, "window_start": "2008-01-01",
         "lookback_days": 100, "generated": "2026-07-11T16:10:05Z", "all_label": "All-time",
         "scope_targets": {"org": ["acme-org"]},
         "data_quality": {"api_rate_limited": False, "api_reset": None},
         "window_labels": ["30d", "all"],
         "person_options": [{"login": "alice", "name": "Alice A", "company": "Acme", "emails": "alice@acme.com"}],
         "person_companies": ["Acme"], "person": "alice"}


class PersonJsonEnvelopeTest(unittest.TestCase):
    def test_no_person_is_picker_only(self):
        meta = dict(_META); meta["person"] = None
        out = render.person_json(None, meta)
        self.assertTrue(out["ok"])
        self.assertIsNone(out["dashboard"])
        self.assertIsNone(out["person"])
        self.assertEqual(out["personCompanies"], ["Acme"])
        self.assertEqual(out["personOptions"][0]["login"], "alice")
        # envelope mirrors people_json
        self.assertEqual(out["meta"]["generatedText"], "2026-07-11 16:10")
        self.assertEqual(out["period"], {"preset": "all", "label": "All-time", "from": None, "to": None})
        self.assertEqual(out["periodPresets"], [{"key": "30d", "label": "30 days"},
                                                 {"key": "all", "label": "All-time"}])
        self.assertEqual(out["scopeTargets"], {"org": ["acme-org"]})

    def test_with_dash_builds_dashboard(self):
        out = render.person_json(_dash(), _META)
        d = out["dashboard"]
        self.assertFalse(d["empty"])
        self.assertEqual(d["login"], "alice")
        self.assertEqual(d["header"]["name"], "Alice A")
        self.assertEqual(d["header"]["rank"], 2)
        self.assertTrue(d["header"]["isMember"])
        self.assertEqual(d["ghProfile"]["location"], "Berlin")

    def test_kpi_tiles_shape_and_formatting(self):
        d = render.person_json(_dash(), _META)["dashboard"]
        tiles = d["kpis"]
        self.assertEqual(len(tiles), 7)
        self.assertEqual(tiles[0]["icon"], "commit")
        self.assertEqual(tiles[0]["value"], "20")
        self.assertEqual(tiles[0]["sub"], "25% of org this period")   # _pct drops trailing .0
        self.assertEqual(tiles[1]["value"], "1.5K")                    # meaningful LOC via _loc
        self.assertEqual(tiles[1]["sub"], "30% of org")
        self.assertEqual(tiles[2]["sub"], "4 merged · 20% of org")
        # drill present (non-zero counts), data-* order preserved
        self.assertEqual(tiles[0]["drill"], {"drill": "commit", "author": "alice", "scope": "none"})
        self.assertEqual(tiles[4]["drill"], {"drill": "issue", "flag": "is_bug", "author": "alice", "scope": "none"})

    def test_drill_absent_when_count_zero(self):
        d = render.person_json(_dash(profile={**_dash()["profile"],
                                              "totals": {**_dash()["profile"]["totals"], "bugs": 0}}), _META)["dashboard"]
        self.assertIsNone(d["kpis"][4]["drill"])

    def test_impact_preformatted(self):
        im = render.person_json(_dash(), _META)["dashboard"]["impact"]
        self.assertEqual(im["survHuman"], "2.0K")
        self.assertEqual(im["mergeRateText"], "83.0%")   # round(100*25/30) -> "83.0" + "%"
        self.assertEqual(im["ttmText"], "4.0 h")
        self.assertEqual(im["commitsText"], "1,234")     # _num with separators
        self.assertEqual(im["reviews"], 40)              # raw int (statrow renders as-is)

    def test_repo_types_and_codespecs(self):
        d = render.person_json(_dash(), _META)["dashboard"]
        rt = d["repoTypes"]
        self.assertEqual(rt["total"], 20)
        self.assertEqual(rt["types"][0]["pctText"], "75.0")   # 100*15/20 |round -> "75.0"
        cs = d["codeSpecs"]
        self.assertFalse(cs["empty"])
        self.assertEqual(cs["pa"], "90.0")
        self.assertEqual(cs["aNum"], "18")

    def test_empty_dashboard_when_no_activity(self):
        prof = {"login": "ghost", "totals": {"commits": 0, "prs": 0, "issues": 0},
                "shares": {}, "spark": {}, "repos": [], "split": {"types": [], "total": 0},
                "elements": [], "work_type": [], "mix": {"code": 0, "specs": 0}}
        out = render.person_json(_dash(login="ghost", profile=prof, alltime={}), _META)
        self.assertTrue(out["dashboard"]["empty"])
        self.assertEqual(out["dashboard"]["login"], "ghost")

    def test_weekly_passthrough(self):
        d = render.person_json(_dash(), _META)["dashboard"]
        self.assertEqual(d["weekly"]["grand"]["commits"], 5)
        self.assertEqual(d["weekly"]["columns"][0]["name"], "gears-core")


class PersonJsonHelpersTest(unittest.TestCase):
    def test_jr_matches_jinja_round(self):
        self.assertEqual(render._jr(100 * 3 / 7), "43.0")       # |round -> "43.0", not "43"
        self.assertEqual(render._jr(100 * 4 / 8), "50.0")       # integer result keeps ".0"
        self.assertEqual(render._jr(100 * 3 / 7, 1), "42.9")    # |round(1)
        self.assertEqual(render._jr(0.126, 2), "0.13")
        self.assertEqual(render._jr(4.0, 1), "4.0")

    def test_split2_empty(self):
        self.assertEqual(render._split2_json(0, 0), {"empty": True})


def _collected_rows(db: Path) -> int:
    """Commits in `db`, or 0 if it cannot be read or has no schema yet.

    Existence is not usability. store.connect() CREATES the store lazily, complete
    with schema, so any test run in a checkout without collected data leaves a valid
    but EMPTY history/report.db behind — after which a file-existence guard stops
    skipping and the tests below fail instead, with a SystemExit from load_data()
    ("No collected data in the store yet") rather than anything about themselves.
    That is what happened in every git worktree and would happen in CI.
    """
    if not db.exists():
        return 0
    try:
        conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return 0
    try:
        return conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0]
    except sqlite3.Error:
        return 0                     # no schema, or not a database at all
    finally:
        conn.close()


class PersonRealDbEndpointTest(unittest.TestCase):
    """End-to-end against the repo's real history/report.db — the with/without-person
    shapes over genuine collected data, which is a different question from the seeded
    fixture the rest of the suite runs on (see tests/_state.py). Skipped when this
    checkout has no collected data, since there is then nothing real to check."""

    @classmethod
    def setUpClass(cls):
        if not _collected_rows(REAL_DB):
            raise unittest.SkipTest(f"{REAL_DB} has no collected data")
        cls._env = patch.dict(os.environ, {"REPORT_DB": str(REAL_DB)})
        cls._env.start()
        import server
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls._env.stop()

    def _get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=60) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_no_person_returns_picker(self):
        status, body = self._get("/api/report/person")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIsNone(body["dashboard"])
        self.assertGreater(len(body["personOptions"]), 0)
        for key in ("meta", "period", "periodPresets", "scope", "scopeTargets", "dataQuality"):
            self.assertIn(key, body)

    def test_with_person_returns_dashboard(self):
        # pick the biggest contributor present in the picker options
        _, picker = self._get("/api/report/person")
        login = picker["personOptions"][0]["login"]
        status, body = self._get(f"/api/report/person?person={login}")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["person"], login)
        self.assertIsNotNone(body["dashboard"])
        d = body["dashboard"]
        if not d["empty"]:
            self.assertEqual(d["login"], login)
            self.assertEqual(len(d["kpis"]), 7)
            self.assertIn("weekly", d)


class PersonApiValidationTest(unittest.TestCase):
    """Validation paths on an isolated temp DB — same harness as PeopleApiEndpointTest."""

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

    def test_invalid_person_returns_400(self):
        status, body = self._get("/api/report/person?person=bad!login")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "invalid person")

    def test_no_data_returns_404(self):
        status, body = self._get("/api/report/person")
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "no collected data")

    def test_invalid_preset_returns_400(self):
        status, body = self._get("/api/report/person?p=nonsense")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])


if __name__ == "__main__":
    unittest.main()
