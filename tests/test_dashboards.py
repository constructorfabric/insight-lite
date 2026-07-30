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

import dashboards
import render
import store
import view_registry


def _store(tmp):
    with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
        return store, store.connect()


class CatalogBindingTest(unittest.TestCase):
    def test_dashboard_views_are_flagged_and_have_binding(self):
        dv = {v["name"]: v for v in view_registry.dashboard_views()}
        self.assertIn("kpi_tile", dv)
        self.assertIn("data_table", dv)
        self.assertIn("line_chart", dv)
        self.assertNotIn("bar_cell", dv)  # sub-part, not a standalone panel
        for v in dv.values():
            self.assertIn("shape", v["binding"])


class ValidateSpecTest(unittest.TestCase):
    def _spec(self, **over):
        s = {"title": "T", "panels": [
            {"id": "p1", "component": "kpi_tile",
             "source": {"tool": "contribution", "field": "totals.commits"}}]}
        s.update(over)
        return s

    def test_valid_spec_passes(self):
        ok, err = dashboards.validate_spec(self._spec())
        self.assertTrue(ok, err); self.assertIsNone(err)

    def test_title_required(self):
        ok, err = dashboards.validate_spec({"panels": []})
        self.assertFalse(ok); self.assertIn("title", err)

    def test_unknown_tool_rejected(self):
        ok, err = dashboards.validate_spec(self._spec(panels=[
            {"id": "p1", "component": "kpi_tile", "source": {"tool": "nope"}}]))
        self.assertFalse(ok); self.assertIn("tool", err)

    def test_unknown_component_rejected(self):
        ok, err = dashboards.validate_spec(self._spec(panels=[
            {"id": "p1", "component": "made_up", "source": {"tool": "contribution"}}]))
        self.assertFalse(ok); self.assertIn("component", err)

    def test_bad_pin_scope_rejected(self):
        ok, err = dashboards.validate_spec(self._spec(panels=[
            {"id": "p1", "component": "kpi_tile", "source": {"tool": "contribution"},
             "pin": {"scope": "person:bob"}}]))
        self.assertFalse(ok); self.assertIn("scope", err)

    def test_sql_query_source_rejected(self):
        ok, err = dashboards.validate_spec(self._spec(panels=[
            {"id": "p1", "component": "kpi_tile", "source": {"tool": "sql_query"}}]))
        self.assertFalse(ok); self.assertIn("tool", err)


class DashboardStoreTest(unittest.TestCase):
    def test_create_get_update_list_delete(self):
        with TemporaryDirectory() as tmp:
            st, conn = _store(tmp)

            did = st.create_dashboard(conn, "tester", "My board",
                                      {"title": "My board", "panels": []})
            self.assertTrue(did)
            row = st.get_dashboard(conn, did)
            self.assertEqual(row["owner_login"], "tester")
            self.assertEqual(row["spec"]["title"], "My board")
            self.assertEqual(row["visibility"], "private")

            st.update_dashboard(conn, did, title="Renamed",
                                spec={"title": "Renamed", "panels": []},
                                visibility="shared")
            row = st.get_dashboard(conn, did)
            self.assertEqual(row["title"], "Renamed")
            self.assertEqual(row["visibility"], "shared")

            mine = st.list_dashboards(conn, "tester")
            self.assertTrue(any(d["id"] == did for d in mine))

            st.delete_dashboard(conn, did)
            self.assertIsNone(st.get_dashboard(conn, did))

            conn.close()


class RenderMacroTest(unittest.TestCase):
    def test_render_kpi_tile_macro(self):
        html = render.render_panel_macro("kpi_tile", {"value": "1,204", "label": "Commits"})
        self.assertIn("1,204", html)
        self.assertIn("Commits", html)

    def test_binds_by_keyword_not_position(self):
        # label listed before value; must still bind by name
        html = render.render_panel_macro("kpi_tile", {"label": "Commits", "value": "1,204"})
        self.assertIn("1,204", html)
        self.assertIn("Commits", html)

    def test_rejects_bad_macro_name(self):
        with self.assertRaises(ValueError):
            render.render_panel_macro("kpi_tile() import os; os.system('x') #", {"value": "1"})


class ResolverTest(unittest.TestCase):
    def test_scalar_panel_renders_a_number(self):
        panel = {"id": "p1", "component": "kpi_tile", "title": "Commits",
                 "source": {"tool": "contribution", "field": "totals.commits"}}
        html = dashboards.render_panel(panel, scope="", period="all")
        self.assertRegex(html, r"\d")
        self.assertIn("Commits", html)

    def test_unknown_field_degrades_not_raises(self):
        panel = {"id": "p1", "component": "kpi_tile", "title": "X",
                 "source": {"tool": "contribution", "field": "totals.NOPE"}}
        html = dashboards.render_panel(panel, scope="", period="all")
        self.assertIn("n/a", html.lower())

    def test_line_chart_panel_renders_without_error(self):
        panel = {"id": "p1", "component": "line_chart", "title": "Commits over time",
                 "source": {"tool": "trend", "field": "commit_rows"}}
        html = dashboards.render_panel(panel, scope="", period="all")
        self.assertTrue(html.strip())
        self.assertNotIn("dp-err", html)

    def test_data_table_panel_renders_a_table(self):
        panel = {"id": "p1", "component": "data_table", "title": "By company",
                 "source": {"tool": "contribution", "field": "by_company"}}
        html = dashboards.render_panel(panel, scope="", period="all")
        self.assertIn("<table", html)
        self.assertNotIn("dp-err", html)

    def test_non_dict_panel_degrades(self):
        for bad in (None, ["x"]):
            html = dashboards.render_panel(bad, scope="", period="all")
            self.assertIsInstance(html, str)
            self.assertIn("dp-err", html)

    def test_error_tile_escapes_title(self):
        panel = {"id": "p1", "component": "line_chart",
                 "title": "<img src=x onerror=alert(1)>",
                 "source": {"tool": "contribution", "field": "totals.commits"}}
        html = dashboards.render_panel(panel, scope="", period="all")
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)

    def test_sql_query_tool_rejected_at_render_time(self):
        # render_panel must enforce the safe-tool allowlist itself, not just
        # validate_spec — preview and any other write path skip validate_spec.
        panel = {"id": "p", "component": "data_table",
                 "source": {"tool": "sql_query",
                            "params": {"sql": "SELECT login FROM person"},
                            "field": "rows"}}
        html = dashboards.render_panel(panel, scope="", period="all")
        self.assertIn("dp-err", html)
        self.assertNotIn("<table", html)


class ApiValidationTest(unittest.TestCase):
    def test_bad_spec_is_rejected_by_validator(self):
        ok, err = dashboards.validate_spec({"panels": "not a list", "title": "x"})
        self.assertFalse(ok)


class EndToEndTest(unittest.TestCase):
    def test_create_then_render_each_panel(self):
        spec = {"title": "E2E", "panels": [
            {"id": "a", "component": "kpi_tile",
             "source": {"tool": "contribution", "field": "totals.commits"}},
            {"id": "b", "component": "data_table",
             "source": {"tool": "contribution", "field": "by_company"}},
        ]}
        ok, err = dashboards.validate_spec(spec)
        self.assertTrue(ok, err)
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                try:
                    did = store.create_dashboard(conn, "tester", "E2E", spec)
                    d = store.get_dashboard(conn, did)
                    for p in d["spec"]["panels"]:
                        html = dashboards.render_panel(p, scope="", period="all")
                        self.assertTrue(html, p["id"])
                        self.assertNotIn("dp-err", html, p["id"])
                finally:
                    conn.close()


class CatalogEndpointDataTest(unittest.TestCase):
    def test_dashboard_catalog_lists_components_and_safe_tools(self):
        cat = dashboards.dashboard_catalog()
        names = {c["name"] for c in cat["components"]}
        self.assertIn("kpi_tile", names)
        self.assertIn("data_table", names)
        self.assertIn("contribution", cat["tools"])
        self.assertNotIn("sql_query", cat["tools"])
        for c in cat["components"]:
            self.assertIn("kind", c)


class DashboardEditorEndpointTest(unittest.TestCase):
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

    def _get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        return urllib.request.urlopen(req)

    def _post(self, path, body, headers=None):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **(headers or {})})
        return urllib.request.urlopen(req)

    def test_catalog_endpoint(self):
        resp = self._get("/api/dashboard/catalog",
                          headers={"X-Forwarded-Preferred-Username": "tester"})
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.read())
        self.assertTrue(body["ok"])
        self.assertTrue(body["components"])
        self.assertIn("contribution", body["tools"])
        self.assertNotIn("sql_query", body["tools"])

    def test_measures_endpoint(self):
        resp = self._get("/api/dashboard/measures",
                          headers={"X-Forwarded-Preferred-Username": "tester"})
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.read())
        self.assertTrue(body["ok"])
        self.assertTrue(body["measures"])
        for m in body["measures"]:
            self.assertIn("label", m)
            self.assertIn("category", m)
            self.assertIn("shape", m)
            self.assertIn("source", m)
        # advanced field dropdown data rides along in the same payload
        self.assertIn("tool_fields", body)
        self.assertIn("contribution", body["tool_fields"])

    def test_preview_panel_endpoint(self):
        resp = self._post(
            "/api/dashboard/preview-panel",
            {"panel": {"id": "p", "component": "kpi_tile",
                       "source": {"tool": "contribution", "field": "totals.commits"}},
             "period": "all"},
            headers={"X-Forwarded-Preferred-Username": "tester"})
        self.assertEqual(resp.status, 200)
        html = resp.read().decode()
        self.assertIn('class="kpi"', html)
        self.assertNotIn("dp-err", html)

    def test_preview_panel_endpoint_rejects_sql_query(self):
        resp = self._post(
            "/api/dashboard/preview-panel",
            {"panel": {"id": "p", "component": "data_table",
                       "source": {"tool": "sql_query",
                                  "params": {"sql": "SELECT login, emails FROM person LIMIT 3"},
                                  "field": "rows"}},
             "period": "all"},
            headers={"X-Forwarded-Preferred-Username": "tester"})
        self.assertEqual(resp.status, 200)
        html = resp.read().decode()
        self.assertIn("dp-err", html)
        self.assertNotIn("<table", html)
        self.assertNotIn("person", html.lower())


class DashboardEditorFlowTest(unittest.TestCase):
    """End-to-end HTTP coverage of the editor flow: create → update (owner-only)
    → preview-panel → edit page (owner-only) → /dashboards list (Edit link only
    on rows the viewer owns). Same ThreadingHTTPServer + isolated REPORT_DB
    harness as DashboardEditorEndpointTest above."""

    OWNER = "owner"
    INTRUDER = "intruder"

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._env = patch.dict(os.environ, {"REPORT_DB": str(Path(self._tmp.name) / "t.db")})
        self._env.start()
        import server
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        # _resolve_viewer() maps the header identity to a login via
        # store.person_login_for(), which requires a matching `person` row —
        # seed the two logins the flow tests use as distinct viewers.
        import store
        conn = store.connect()
        try:
            for login in (self.OWNER, self.INTRUDER):
                conn.execute("INSERT INTO person (login, name) VALUES (?, ?)", (login, login))
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        self.httpd.shutdown()
        self._env.stop()
        self._tmp.cleanup()

    def _get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        return urllib.request.urlopen(req)

    def _post(self, path, body, headers=None):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **(headers or {})})
        return urllib.request.urlopen(req)

    @staticmethod
    def _hdr(user):
        return {"X-Forwarded-Preferred-Username": user}

    def _status_body(self, fn, *a, **kw):
        """Run a request that may 4xx; urlopen raises HTTPError for non-2xx, so
        normalize both paths to (status, body bytes)."""
        try:
            resp = fn(*a, **kw)
            return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_create_dashboard(self):
        status, body = self._status_body(
            self._post, "/api/dashboard",
            {"spec": {"title": "My board", "panels": []}}, self._hdr(self.OWNER))
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertTrue(data.get("id"))

    def test_update_dashboard_owner_only(self):
        status, body = self._status_body(
            self._post, "/api/dashboard",
            {"spec": {"title": "Board", "panels": []}}, self._hdr(self.OWNER))
        self.assertEqual(status, 200)
        did = json.loads(body)["id"]

        spec = {"title": "Board", "panels": [
            {"id": "p1", "component": "kpi_tile",
             "source": {"tool": "contribution", "field": "totals.commits"}},
            {"id": "p2", "component": "data_table",
             "source": {"tool": "contribution", "field": "by_company"}},
        ]}

        # owner can update
        status, body = self._status_body(
            self._post, f"/api/dashboard/{did}", {"spec": spec}, self._hdr(self.OWNER))
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])

        # a different user gets 404 (update is owner-only, not "forbidden" —
        # avoids confirming the id exists to non-owners)
        status, body = self._status_body(
            self._post, f"/api/dashboard/{did}", {"spec": spec}, self._hdr(self.INTRUDER))
        self.assertEqual(status, 404)

    def test_preview_panel_kpi_tile(self):
        status, body = self._status_body(
            self._post, "/api/dashboard/preview-panel",
            {"panel": {"id": "p", "component": "kpi_tile",
                       "source": {"tool": "contribution", "field": "totals.commits"}},
             "period": "all"}, self._hdr(self.OWNER))
        self.assertEqual(status, 200)
        self.assertIn('class="kpi"', body.decode())

    def test_edit_page_owner_only(self):
        status, body = self._status_body(
            self._post, "/api/dashboard",
            {"spec": {"title": "Board", "panels": []}}, self._hdr(self.OWNER))
        self.assertEqual(status, 200)
        did = json.loads(body)["id"]

        # The editor is a React route: the widget picker, the measure-first modal
        # and the catalog fetches live in the Vite bundle. The server-Jinja editor
        # this used to also assert against went with the rest of the legacy layer;
        # what the server still owns is the shared sidebar chrome around it.
        status, body = self._status_body(
            self._get, f"/dashboard/{did}/edit", self._hdr(self.OWNER))
        self.assertEqual(status, 200)
        self.assertIn('class="sidebar"', body.decode())

        # owner-only gate applies to BOTH paths — an intruder gets 404 on the
        # React route too.
        status, body = self._status_body(
            self._get, f"/dashboard/{did}/edit", self._hdr(self.INTRUDER))
        self.assertEqual(status, 404)

    def test_dashboards_list_edit_link_only_for_owned_rows(self):
        status, body = self._status_body(
            self._post, "/api/dashboard",
            {"spec": {"title": "Mine", "panels": []}}, self._hdr(self.OWNER))
        self.assertEqual(status, 200)
        owned_id = json.loads(body)["id"]

        # seed a board shared by someone else directly against the same DB the
        # server uses (REPORT_DB is patched for this test's whole lifetime)
        import store
        conn = store.connect()
        try:
            shared_id = store.create_dashboard(
                conn, "someone-else", "Shared board",
                {"title": "Shared board", "panels": []}, visibility="shared")
        finally:
            conn.close()

        # /dashboards is the React shell now; the server-rendered list whose
        # Edit-link-per-owner logic this used to assert went with the legacy layer.
        # The rule lives in the JSON instead: it carries the viewer login and both
        # boards, and the client gates the Edit link on owner == login.
        status, body = self._status_body(
            self._get, "/api/manage/dashboards.json", self._hdr(self.OWNER))
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["login"], self.OWNER)
        ids = {d["id"] for d in data["dashboards"]}
        self.assertIn(owned_id, ids)
        self.assertIn(shared_id, ids)


class MeasuresTest(unittest.TestCase):
    def test_measures_grouped_and_labelled(self):
        ms = dashboards.measures()
        self.assertTrue(ms)
        for k in ("label", "category", "shape", "source", "component"):
            self.assertIn(k, ms[0])
        self.assertIn(ms[0]["source"]["tool"], dashboards._DASHBOARD_TOOLS)
        commits = [m for m in ms if m["source"] == {"tool": "contribution", "field": "totals.commits"}]
        self.assertTrue(commits, "expected a contribution/totals.commits measure")
        self.assertEqual(commits[0]["shape"], "scalar")
        self.assertTrue(commits[0]["category"])

    def test_internal_sample_counters_are_dropped_but_real_metrics_survive(self):
        # Curation is a denylist: only internal sample-size/helper counters
        # (n_*, *_n, min_items) are hidden — legitimate scalars like prs_total /
        # issues_total ARE surfaced (they were wrongly dropped by the old
        # registry-name allowlist). The advanced tool+field fallback still
        # reaches the hidden ones.
        ms = dashboards.measures()
        sources = {(m["source"]["tool"], m["source"]["field"]) for m in ms}
        must_hide = {
            ("delivery", "pr_ttfr_n"), ("flow", "bounced_n"), ("flow", "reopened_n"),
            ("flow", "rereq_n"), ("flow", "min_items"),
            ("flow", "n_prs"), ("flow", "n_issues"), ("flow", "n_items"),
        }
        leaked = must_hide & sources
        self.assertFalse(leaked, f"internal counters leaked through: {leaked}")
        must_show = {
            ("delivery", "prs_total"), ("delivery", "issues_total"),
            ("delivery", "issues_closed"),
        }
        missing = must_show - sources
        self.assertFalse(missing, f"legitimate scalars wrongly dropped: {missing}")
        # no bare "N <word>" / lone-"n"-suffix / "Min items" cryptic label survives.
        for m in ms:
            self.assertNotRegex(m["label"], r"^N [A-Za-z]|^Min items$| n$",
                                 f"cryptic-looking label survived curation: {m}")

    def test_flow_rate_survives_via_alias(self):
        ms = dashboards.measures()
        matches = [m for m in ms if m["source"] == {"tool": "flow", "field": "reopen_rate"}]
        self.assertTrue(matches, "expected flow/reopen_rate to survive via the metric alias")
        self.assertIn("Flow", matches[0]["category"])

    def test_by_company_categorised_via_alias(self):
        ms = dashboards.measures()
        matches = [m for m in ms
                   if m["source"] == {"tool": "contribution", "field": "by_company"}]
        self.assertTrue(matches, "expected a contribution/by_company measure")
        self.assertEqual(matches[0]["category"], "Company & concentration")

    def test_tool_fields_is_uncurated_superset(self):
        # tool_fields() feeds the editor's advanced field dropdown: grouped by
        # tool, each {field,label,shape,component}, and a SUPERSET of measures()
        # — it keeps the internal counters measures() curates out.
        tf = dashboards.tool_fields()
        self.assertIn("contribution", tf)
        for f in tf["contribution"]:
            for k in ("field", "label", "shape", "component"):
                self.assertIn(k, f)
        deliv = {f["field"] for f in tf.get("delivery", [])}
        self.assertIn("prs_total", deliv, "advanced list must keep curated-out counters")
        # a measure that survived curation is also present in the raw field list
        commits = {f["field"] for f in tf.get("contribution", [])}
        self.assertIn("totals.commits", commits)

    def test_measures_payload_bundles_both(self):
        pl = dashboards.measures_payload()
        self.assertEqual(pl["measures"], dashboards.measures())
        self.assertEqual(pl["tool_fields"], dashboards.tool_fields())


class DashboardPageChromeTest(unittest.TestCase):
    """A dashboard page must carry the Vega bundle same-origin (M-T1) and the
    Vega-Lite chart CSS (.vl-panel + the themed tooltip), because a panel's chart
    renders into it.

    These used to assert against render_dashboard_page/-_editor, the server-Jinja
    pages. Those went with the legacy layer — both routes are React now — so the
    claim moves to the shell that serves them, which is where `vega=True` decides
    whether the bundle and its CSS are injected at all."""

    def _shell(self, entry):
        import render
        return render.render_spa_page(entry, "dashboards", "T", vega=True,
                                      bootstrap={"id": "x", "title": "T", "spec": {}})

    def test_dashboard_view_carries_vega_and_chart_css(self):
        html = self._shell("dashboard")
        self.assertIn("/assets/vega/vega-embed.min.js", html)
        self.assertIn(".vl-panel", html)
        self.assertIn("vg-tooltip", html)

    def test_editor_carries_vega_and_chart_css(self):
        html = self._shell("dashboard-editor")
        self.assertIn("/assets/vega/vega-embed.min.js", html)
        self.assertIn(".vl-panel", html)

    def test_a_page_without_vega_does_not_ship_the_bundle(self):
        """The flag has to mean something: the same shell without it must not carry
        828 KB of charting library on a page that draws no charts."""
        import render
        html = render.render_spa_page("dashboards", "dashboards", "T")
        self.assertNotIn("/assets/vega/vega-embed.min.js", html)


class VegaAssetEndpointTest(unittest.TestCase):
    """Same ThreadingHTTPServer + isolated REPORT_DB harness as
    DashboardEditorEndpointTest — the Vega bundle route sits next to the font
    route (public, no auth header needed) in server.py's do_GET chain."""

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

    def _get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        return urllib.request.urlopen(req)

    def test_vega_asset_served(self):
        resp = self._get("/assets/vega/vega-lite.min.js")
        self.assertEqual(resp.status, 200)
        self.assertIn("javascript", resp.headers.get("Content-Type", ""))

    def test_vega_asset_rejects_unknown(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/assets/vega/evil.min.js")
        self.assertEqual(cm.exception.code, 404)

    def test_vega_asset_rejects_path_traversal(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/assets/vega/../../server.py")
        self.assertIn(cm.exception.code, (400, 404))


class WidgetV2Test(unittest.TestCase):
    def test_normalize_legacy_panel(self):
        p = dashboards._normalize_panel(
            {"id": "p1", "component": "kpi_tile",
             "source": {"tool": "contribution", "field": "totals.commits"}, "title": "C"})
        self.assertEqual(p["viz"], "number")
        self.assertEqual(p["data"], {"tool": "contribution", "fields": ["totals.commits"]})

    def test_normalize_line_chart_legacy(self):
        p = dashboards._normalize_panel(
            {"id": "p", "component": "line_chart", "source": {"tool": "trend", "field": "commit_rows"}})
        self.assertEqual(p["viz"], "line")
        self.assertEqual(p["data"]["fields"], ["commit_rows"])

    def test_v2_panel_passthrough(self):
        v2 = {"id": "p", "viz": "area", "data": {"tool": "trend", "fields": ["commit_rows", "loc_rows"]}}
        self.assertEqual(dashboards._normalize_panel(v2)["viz"], "area")

    def test_validate_v2_spec(self):
        ok, err = dashboards.validate_spec(
            {"title": "D", "panels": [
                {"id": "p", "viz": "line", "data": {"tool": "trend", "fields": ["commit_rows"]}}]})
        self.assertTrue(ok, err)

    def test_validate_rejects_bad_viz(self):
        ok, _ = dashboards.validate_spec(
            {"title": "D", "panels": [{"id": "p", "viz": "sankey", "data": {"tool": "trend", "fields": ["x"]}}]})
        self.assertFalse(ok)

    def test_validate_rejects_multi_field_number(self):
        ok, _ = dashboards.validate_spec(
            {"title": "D", "panels": [{"id": "p", "viz": "number",
                                        "data": {"tool": "contribution", "fields": ["a", "b"]}}]})
        self.assertFalse(ok)

    def test_validate_rejects_sql_tool_v2(self):
        ok, _ = dashboards.validate_spec(
            {"title": "D", "panels": [{"id": "p", "viz": "table", "data": {"tool": "sql_query", "fields": ["x"]}}]})
        self.assertFalse(ok)

    def _spec_of(self, html):
        import json, re
        m = re.search(r'class="vl-spec"[^>]*>(.*?)</script>', html, re.S)
        self.assertTrue(m, f"no vl-spec in: {html[:200]}")
        return json.loads(m.group(1).replace("<\\/", "</"))

    def test_render_multi_series_line(self):
        html = dashboards.render_panel(
            {"id": "p", "viz": "line", "title": "Trend",
             "data": {"tool": "trend", "fields": ["commit_rows", "loc_rows"]}}, "", "all")
        self.assertNotIn("dp-err", html)
        self.assertIn("vl-panel", html)
        self.assertEqual(self._spec_of(html)["encoding"]["color"]["field"], "series")

    def test_render_legacy_still_works(self):
        html = dashboards.render_panel(
            {"id": "p", "component": "kpi_tile", "title": "C",
             "source": {"tool": "contribution", "field": "totals.commits"}}, "", "all")
        self.assertNotIn("dp-err", html)

    def test_viz_options_by_shape(self):
        # scalars can be a single Number OR combined into a categorical chart
        # (BI "Measure Values"); series overlay on line/area; breakdowns fill a chart.
        self.assertEqual(dashboards.viz_options("series"), ["line", "area"])
        self.assertIn("pie", dashboards.viz_options("table"))
        scalar = dashboards.viz_options("scalar")
        self.assertIn("number", scalar)
        for v in ("column", "bar", "pie", "table"):
            self.assertIn(v, scalar)


class EditorVizV2Test(unittest.TestCase):
    def test_editor_has_viz_selector_and_series_shelf(self):
        """The editor offers a viz-type choice and a series shelf, and the panel it
        emits carries `viz`. Pinned against the React editor's source: this used to
        read the server-Jinja editor's HTML, which went with the legacy layer, and the
        widget picker now lives entirely in the Vite bundle."""
        from pathlib import Path as _P
        src = (_P(__file__).resolve().parents[1]
               / "frontend/src/pages/DashboardEditor.tsx").read_text()
        self.assertIn("SHAPE_VIZ", src)      # the shape→viz compatibility map
        self.assertIn("viz", src)            # viz-type selector state
        self.assertIn("series", src)         # measures/series shelf


class WidgetFixesTest(unittest.TestCase):
    """Regressions from the W3-T2/T3 reviews."""

    def _spec_of(self, html):
        import json, re
        m = re.search(r'class="vl-spec"[^>]*>(.*?)</script>', html, re.S)
        self.assertTrue(m, f"no vl-spec in: {html[:200]}")
        return json.loads(m.group(1).replace("<\\/", "</"))

    def test_validate_fieldless_legacy_panel_still_saves(self):
        # A pre-v2 panel whose source had no `field` was always accepted (renders
        # n/a); it must stay re-savable, not 400 on the save path.
        ok, err = dashboards.validate_spec(
            {"title": "D", "panels": [
                {"id": "p", "component": "kpi_tile", "source": {"tool": "contribution"}}]})
        self.assertTrue(ok, err)

    def test_validate_rejects_empty_fields_on_v2_panel(self):
        # but a v2-native panel must still name at least one field.
        ok, _ = dashboards.validate_spec(
            {"title": "D", "panels": [
                {"id": "p", "viz": "number", "data": {"tool": "contribution", "fields": []}}]})
        self.assertFalse(ok)

    def test_multi_scalar_column_is_measure_values(self):
        # several scalar measures on a column chart → one bar per measure.
        html = dashboards.render_panel(
            {"id": "p", "viz": "column", "title": "Volume",
             "data": {"tool": "contribution", "fields": ["totals.bugs", "totals.prs", "totals.epics"]}},
            "", "all")
        self.assertNotIn("dp-err", html)
        self.assertIn("vl-panel", html)
        spec = self._spec_of(html)
        mark = spec["mark"]
        self.assertEqual(mark["type"] if isinstance(mark, dict) else mark, "bar")
        self.assertEqual(len(spec["data"]["values"]), 3)

    def test_multi_scalar_pie(self):
        html = dashboards.render_panel(
            {"id": "p", "viz": "pie", "title": "Mix",
             "data": {"tool": "contribution", "fields": ["totals.bugs", "totals.prs"]}}, "", "all")
        self.assertNotIn("dp-err", html)
        self.assertIn("vl-panel", html)
        spec = self._spec_of(html)
        self.assertEqual(spec["mark"]["type"], "arc")

    def test_scalar_table_is_measure_values(self):
        html = dashboards.render_panel(
            {"id": "p", "viz": "table", "title": "T",
             "data": {"tool": "contribution", "fields": ["totals.bugs", "totals.prs"]}}, "", "all")
        self.assertNotIn("dp-err", html)
        self.assertIn("<table", html)
        self.assertIn("Measure", html)

    def test_breakdown_bar_still_works(self):
        # a table-shaped breakdown field still fills a bar chart on its own.
        html = dashboards.render_panel(
            {"id": "p", "viz": "bar", "title": "By company",
             "data": {"tool": "contribution", "fields": ["by_company"]}}, "", "all")
        self.assertNotIn("dp-err", html)
        self.assertIn("vl-panel", html)
        spec = self._spec_of(html)
        mark = spec["mark"]
        self.assertEqual(mark["type"] if isinstance(mark, dict) else mark, "bar")

    def test_validate_multi_scalar_column_ok(self):
        ok, err = dashboards.validate_spec(
            {"title": "D", "panels": [
                {"id": "p", "viz": "column",
                 "data": {"tool": "contribution", "fields": ["totals.bugs", "totals.prs"]}}]})
        self.assertTrue(ok, err)

    def test_validate_v2_panel_with_stray_legacy_keys(self):
        # a v2 panel that still carries a leftover `source`/`component` key must
        # validate as v2, not be rejected as an unknown legacy component.
        ok, err = dashboards.validate_spec(
            {"title": "D", "panels": [
                {"id": "p", "viz": "number",
                 "data": {"tool": "contribution", "fields": ["totals.commits"]},
                 "source": {"tool": "old"}, "component": None}]})
        self.assertTrue(ok, err)


if __name__ == "__main__":
    unittest.main()
