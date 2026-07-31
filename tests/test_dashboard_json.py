"""Resolved-DATA JSON boundary for dashboard panels (WS2-T2).

Covers `dashboards.resolve_panel_data` (the data half of the panel resolver, split
out of `_render_panel`) and the two JSON endpoints that expose it:
  - GET  /api/dashboard/panel.json          (twin of /api/dashboard/panel)
  - POST /api/dashboard/preview-panel.json  (twin of /api/dashboard/preview-panel)

Per-viz data shapes asserted here:
  number → {"value": <number|null>}
  table  → {"columns": [...], "rows": [...]}
  chart  → what to draw from (built server-side by chart_panel_data; has
           "$schema"/"mark"/"layer") — NOT stringified into HTML.

Uses the real REPORT_DB (run with `REPORT_DB=history/report.db`) so the tool
sources resolve to real rows, exactly like the seeded gate dashboard.
"""
import importlib.util
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import dashboards
import store

_CHART_VIZ = ("line", "area", "column", "bar", "pie")


def _load_seed_panels():
    """Import PANELS from frontend/visual/seed_dashboards.py without treating
    frontend/visual as a package (it has no __init__)."""
    root = Path(__file__).resolve().parents[1]
    path = root / "frontend" / "visual" / "seed_dashboards.py"
    spec = importlib.util.spec_from_file_location("seed_dashboards", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PANELS


PANELS = _load_seed_panels()


def _is_chart_data(data) -> bool:
    """A chart panel's payload: `kind` says which of the five pictures, and the rest is
    what to draw it from — a time series (dates + series) or labelled rows. It used to
    be a Vega-Lite spec; the client composes the chart now, so nothing about the
    renderer crosses this boundary."""
    if not isinstance(data, dict) or data.get("kind") not in ("line", "area", "column",
                                                              "bar", "pie"):
        return False
    if data["kind"] in ("line", "area"):
        return isinstance(data.get("dates"), list) and isinstance(data.get("series"), list)
    return isinstance(data.get("rows"), list)


class ResolvePanelDataShapeTest(unittest.TestCase):
    """resolve_panel_data returns {viz,title,pin,data} with the right `data`
    shape per viz — one assertion path per viz covered by the seed spec."""

    def _resolved(self, panel):
        r = dashboards.resolve_panel_data(panel, scope="", period=None)
        # envelope is always {viz,title,pin,data}
        for k in ("viz", "title", "pin", "data"):
            self.assertIn(k, r, f"missing {k} for {panel['viz']}")
        self.assertEqual(r["viz"], panel["viz"])
        self.assertNotIn("error", r["data"], f"unexpected resolve error: {r['data']}")
        return r

    def test_every_seeded_viz_resolves_to_its_shape(self):
        by_viz = {p["viz"]: p for p in PANELS}
        # the seed covers the whole dashboard vocabulary
        self.assertEqual(set(by_viz), {"number", "table", *_CHART_VIZ})

        for viz, panel in by_viz.items():
            with self.subTest(viz=viz):
                data = self._resolved(panel)["data"]
                if viz == "number":
                    self.assertIn("value", data)
                    self.assertTrue(isinstance(data["value"], (int, float)) or data["value"] is None)
                elif viz == "table":
                    self.assertIn("columns", data)
                    self.assertIn("rows", data)
                    self.assertIsInstance(data["columns"], list)
                    self.assertIsInstance(data["rows"], list)
                else:  # chart family → what to draw, not how
                    self.assertTrue(_is_chart_data(data),
                                    f"{viz} is not chart data: {list(data)[:5]}")

    def test_number_value_is_a_scalar(self):
        panel = next(p for p in PANELS if p["viz"] == "number")
        data = self._resolved(panel)["data"]
        self.assertIsInstance(data["value"], (int, float))

    def test_table_columns_and_rows_align(self):
        panel = next(p for p in PANELS if p["viz"] == "table")
        data = self._resolved(panel)["data"]
        self.assertTrue(data["rows"], "seed table panel expected real rows")
        col_keys = {c["key"] for c in data["columns"]}
        self.assertTrue(col_keys.issubset(set(data["rows"][0])))

    def test_chart_spec_is_dict_not_string(self):
        panel = next(p for p in PANELS if p["viz"] in _CHART_VIZ)
        data = self._resolved(panel)["data"]
        self.assertIsInstance(data, dict)
        self.assertNotIsInstance(data, str)


class ResolvePanelDataErrorTest(unittest.TestCase):
    """Failure paths return {"error": …} in `data` (mirroring the dp-err messages)
    instead of raising — the JSON boundary never 500s on a bad panel."""

    def test_unknown_viz(self):
        r = dashboards.resolve_panel_data(
            {"id": "p", "viz": "sankey", "data": {"tool": "trend", "fields": ["x"]}}, "", "all")
        self.assertIn("error", r["data"])
        self.assertIn("unknown viz", r["data"]["error"])

    def test_disallowed_tool(self):
        r = dashboards.resolve_panel_data(
            {"id": "p", "viz": "table",
             "data": {"tool": "sql_query", "params": {"sql": "SELECT 1"}, "fields": ["rows"]}},
            "", "all")
        self.assertIn("error", r["data"])
        self.assertIn("tool not allowed", r["data"]["error"])

    def test_number_missing_field_is_null_not_error(self):
        # a resolved-but-non-numeric field degrades to value=None (HTML shows n/a),
        # matching _render_panel; only a fieldless number is an error.
        r = dashboards.resolve_panel_data(
            {"id": "p", "viz": "number", "title": "X",
             "data": {"tool": "contribution", "fields": ["totals.NOPE"]}}, "", "all")
        self.assertEqual(r["data"], {"value": None})

    def test_number_no_field(self):
        r = dashboards.resolve_panel_data(
            {"id": "p", "viz": "number", "title": "X",
             "data": {"tool": "contribution", "fields": []}}, "", "all")
        self.assertIn("error", r["data"])
        self.assertIn("no field", r["data"]["error"])


class DashboardJsonEndpointTest(unittest.TestCase):
    """The two JSON endpoints return 200 + the resolved envelope. Runs against the
    ambient REPORT_DB (history/report.db) — a shared dashboard is seeded in setUp
    and dropped in tearDown so the GET-by-id path has a target."""

    DID = "dash_json_test_ws2t2"
    OWNER = "gate"

    def setUp(self):
        # use whatever REPORT_DB the suite was launched with (history/report.db);
        # do NOT patch it — the resolver needs real rows.
        self._db = os.environ.get("REPORT_DB")
        conn = store.connect()
        try:
            store.delete_dashboard(conn, self.DID)
            import json as _json
            from datetime import datetime, timezone
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            spec = {"title": "JSON test", "panels": PANELS}
            conn.execute(
                "INSERT INTO dashboard (id, owner_login, title, visibility, spec,"
                " created_ts, updated_ts) VALUES (?,?,?,?,?,?,?)",
                (self.DID, self.OWNER, "JSON test", "shared",
                 _json.dumps(spec, ensure_ascii=False), ts, ts))
            conn.commit()
        finally:
            conn.close()

        import server
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        conn = store.connect()
        try:
            store.delete_dashboard(conn, self.DID)
        finally:
            conn.close()

    def _get(self, path):
        return urllib.request.urlopen(urllib.request.Request(self.base + path))

    def _post(self, path, body):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req)

    def test_panel_json_get_returns_envelope_per_viz(self):
        for panel in PANELS:
            with self.subTest(viz=panel["viz"]):
                resp = self._get(
                    f"/api/dashboard/panel.json?id={self.DID}&panel={panel['id']}&period=all")
                self.assertEqual(resp.status, 200)
                body = json.loads(resp.read())
                self.assertTrue(body["ok"])
                self.assertEqual(body["viz"], panel["viz"])
                data = body["data"]
                self.assertNotIn("error", data if isinstance(data, dict) else {})
                if panel["viz"] == "number":
                    self.assertIn("value", data)
                elif panel["viz"] == "table":
                    self.assertIn("columns", data)
                    self.assertIn("rows", data)
                else:
                    self.assertTrue(_is_chart_data(data))

    def test_panel_json_get_unknown_id_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/api/dashboard/panel.json?id=nope&panel=p_number&period=all")
        self.assertEqual(cm.exception.code, 404)

    def test_preview_panel_json_post_per_viz(self):
        for panel in PANELS:
            with self.subTest(viz=panel["viz"]):
                resp = self._post("/api/dashboard/preview-panel.json",
                                  {"panel": panel, "period": "all"})
                self.assertEqual(resp.status, 200)
                body = json.loads(resp.read())
                self.assertTrue(body["ok"])
                self.assertEqual(body["viz"], panel["viz"])
                data = body["data"]
                if panel["viz"] == "number":
                    self.assertIn("value", data)
                elif panel["viz"] == "table":
                    self.assertIn("columns", data)
                else:
                    self.assertTrue(_is_chart_data(data))

    def test_preview_panel_json_rejects_sql_query(self):
        resp = self._post("/api/dashboard/preview-panel.json",
                          {"panel": {"id": "p", "viz": "table",
                                     "data": {"tool": "sql_query",
                                              "params": {"sql": "SELECT login FROM person LIMIT 1"},
                                              "fields": ["rows"]}},
                           "period": "all"})
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.read())
        self.assertIn("error", body["data"])
        self.assertIn("tool not allowed", body["data"]["error"])


if __name__ == "__main__":
    unittest.main()
