"""Tests for the report's own usage analytics.

Two layers:
  * UsageStoreTest      — the store writers + usage_summary aggregation, including
    the two accuracy rules the design turns on: the 'all' tab is excluded from the
    per-widget ranking, and unresolved (NULL-login) viewers never appear as a
    persona but still count toward opens.
  * UsageEndpointTest   — the live HTTP surface: the /api/usage beacon resolves the
    viewer SERVER-SIDE from the proxy headers (never the body), rejects cross-origin
    and oversized payloads without ever erroring, and /api/usage-summary aggregates.
"""
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


def _store(tmp):
    import store
    with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
        return store, store.connect()


class UsageStoreTest(unittest.TestCase):
    def test_writers_and_summary(self):
        with TemporaryDirectory() as tmp:
            store, conn = _store(tmp)
            conn.execute("INSERT INTO person(login) VALUES('alice')")
            conn.commit()

            store.record_page_open(conn, "alice", "alice")
            store.record_page_open(conn, None, "anon")        # unresolved viewer
            store.record_usage_events(conn, "alice", "alice", [
                {"kind": "tab", "target": "people"},
                {"kind": "panel", "target": "kpis", "tab": "overview", "session_id": "s1"},
                {"kind": "panel", "target": "people", "tab": "people", "session_id": "s1"},
                {"kind": "panel", "target": "kpis", "tab": "all", "session_id": "s2"},
                {"kind": "bogus", "target": "x"},            # unknown kind dropped
            ])

            s = store.usage_summary(conn, "2000-01-01", "2100-01-01")
            self.assertEqual(s["opens"], 2)
            self.assertEqual(s["unique_personas"], 1)         # anon (NULL) not counted

            widgets = {w["target"]: w for w in s["by_widget"]}
            self.assertIn("kpis", widgets)
            self.assertIn("people", widgets)
            # the all-tab kpis view is excluded → kpis has exactly one view
            self.assertEqual(widgets["kpis"]["views"], 1)

            tabs = {t["target"]: t["views"] for t in s["by_tab"]}
            self.assertEqual(tabs.get("people"), 1)

            self.assertEqual([p["login"] for p in s["by_persona"]], ["alice"])
            self.assertEqual(s["by_persona"][0]["opens"], 1)
            self.assertEqual(s["by_persona"][0]["widgets_seen"], 2)

    def test_drills_and_detail(self):
        with TemporaryDirectory() as tmp:
            store, conn = _store(tmp)
            conn.execute("INSERT INTO person(login) VALUES('alice')")
            conn.execute("INSERT INTO person(login) VALUES('bob')")
            conn.commit()
            store.record_usage_events(conn, "alice", "alice", [
                {"kind": "drill", "target": "prs/merged"},
                {"kind": "panel", "target": "kpis", "tab": "overview"},
            ])
            store.record_usage_events(conn, "bob", "bob", [
                {"kind": "drill", "target": "prs/merged"},
            ])
            store.record_usage_events(conn, None, "anon", [
                {"kind": "panel", "target": "kpis", "tab": "overview"},
            ])

            s = store.usage_summary(conn, "2000-01-01", "2100-01-01")
            drills = {d["target"]: d for d in s["by_drill"]}
            self.assertEqual(drills["prs/merged"]["views"], 2)
            self.assertEqual(drills["prs/merged"]["unique_viewers"], 2)

            # who opened the prs/merged drill → alice + bob
            d = store.usage_detail(conn, "2000-01-01", "2100-01-01", "drill", "prs/merged")
            self.assertEqual({v["who"] for v in d["viewers"]}, {"alice", "bob"})

            # who viewed the kpis widget → alice + the unresolved bucket
            w = store.usage_detail(conn, "2000-01-01", "2100-01-01", "widget", "kpis")
            self.assertEqual({v["who"] for v in w["viewers"]}, {"alice", "(unresolved)"})

            # what did alice engage with
            p = store.usage_detail(conn, "2000-01-01", "2100-01-01", "persona", "alice")
            self.assertEqual({x["target"] for x in p["widgets"]}, {"kpis"})
            self.assertEqual({x["target"] for x in p["drills"]}, {"prs/merged"})

    def test_window_end_day_is_inclusive(self):
        # a naive `ts <= 'YYYY-MM-DD'` would drop an event stamped later that day;
        # usage_summary pads the upper bound to T23:59:59Z, so it must be counted.
        with TemporaryDirectory() as tmp:
            store, conn = _store(tmp)
            conn.execute(
                "INSERT INTO usage_event(ts, kind) VALUES('2026-07-21T18:30:00Z','page')")
            conn.commit()
            s = store.usage_summary(conn, "2026-07-01", "2026-07-21")
            self.assertEqual(s["opens"], 1)
            # outside the window → not counted
            s2 = store.usage_summary(conn, "2026-07-01", "2026-07-20")
            self.assertEqual(s2["opens"], 0)


class UsageEndpointTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._env = patch.dict(os.environ, {"REPORT_DB": str(Path(self._tmp.name) / "t.db")})
        self._env.start()
        import store
        conn = store.connect()
        conn.execute("INSERT INTO person(login) VALUES('alice')")
        conn.commit()
        conn.close()
        import server
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self._env.stop()
        self._tmp.cleanup()

    def _post(self, path, body, headers=None):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **(headers or {})})
        return urllib.request.urlopen(req)

    def _get(self, path):
        return urllib.request.urlopen(self.base + path)

    def test_beacon_attributes_viewer_from_headers_not_body(self):
        # the body LIES about identity; the server must ignore it and trust the
        # proxy header (X-Forwarded-Preferred-Username) instead.
        resp = self._post(
            "/api/usage",
            {"session_id": "s1", "viewer_login": "attacker",
             "events": [{"kind": "panel", "target": "kpis", "tab": "overview"}]},
            headers={"X-Forwarded-Preferred-Username": "alice"})
        self.assertEqual(resp.status, 204)

        import store
        conn = store.connect()
        row = conn.execute(
            "SELECT viewer_login, viewer_ident FROM usage_event WHERE kind='panel'").fetchone()
        conn.close()
        self.assertEqual(row["viewer_login"], "alice")
        self.assertEqual(row["viewer_ident"], "alice")

    def test_beacon_oversized_is_ignored_without_error(self):
        big = {"session_id": "s", "events": [{"kind": "panel", "target": "x" * 70000}]}
        resp = self._post("/api/usage", big)
        self.assertEqual(resp.status, 204)      # never errors
        import store
        conn = store.connect()
        n = conn.execute("SELECT COUNT(*) FROM usage_event").fetchone()[0]
        conn.close()
        self.assertEqual(n, 0)                   # oversized payload not recorded

    def test_beacon_rejects_cross_origin(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/api/usage",
                       {"events": [{"kind": "tab", "target": "people"}]},
                       headers={"Origin": "http://evil.example"})
        self.assertEqual(ctx.exception.code, 403)

    def test_summary_endpoint(self):
        self._post("/api/usage",
                   {"session_id": "s1",
                    "events": [{"kind": "tab", "target": "people"},
                               {"kind": "panel", "target": "kpis", "tab": "overview"}]},
                   headers={"X-Forwarded-Preferred-Username": "alice"})
        s = json.loads(self._get("/api/usage-summary?days=30").read())
        self.assertTrue(s["ok"])
        self.assertEqual({t["target"] for t in s["by_tab"]}, {"people"})
        self.assertEqual({w["target"] for w in s["by_widget"]}, {"kpis"})
        self.assertEqual([p["login"] for p in s["by_persona"]], ["alice"])

    def test_detail_endpoint(self):
        self._post("/api/usage",
                   {"session_id": "s1",
                    "events": [{"kind": "drill", "target": "prs/merged"}]},
                   headers={"X-Forwarded-Preferred-Username": "alice"})
        d = json.loads(self._get("/api/usage-detail?by=drill&key=prs/merged&days=30").read())
        self.assertTrue(d["ok"])
        self.assertEqual([v["who"] for v in d["viewers"]], ["alice"])
        # bad params → 400
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/usage-detail?by=nope&key=x")
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
