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

import render
import shell
import spa


class EntryAssetsTest(unittest.TestCase):
    """spa.entry_assets reads the Vite manifest (keys = entry SRC paths, see
    frontend/vite.config.ts) and resolves hashed URLs under /assets/app/."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        manifest = Path(self._tmp.name) / "manifest.json"
        manifest.write_text(json.dumps({
            "src/entries/whatsnew.tsx": {
                "file": "assets/whatsnew-BZ53WYBJ.js",
                "name": "whatsnew",
                "src": "src/entries/whatsnew.tsx",
                "isEntry": True,
                "css": ["assets/whatsnew-BRwL1u47.css"],
            },
        }), encoding="utf-8")
        self._patch = patch.object(spa, "MANIFEST_PATH", manifest)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_known_entry_resolves_hashed_urls(self):
        result = spa.entry_assets("whatsnew")
        self.assertEqual(result, {
            "js": "/assets/app/assets/whatsnew-BZ53WYBJ.js",
            "css": ["/assets/app/assets/whatsnew-BRwL1u47.css"],
        })

    def test_missing_entry_returns_none(self):
        self.assertIsNone(spa.entry_assets("nope"))

    def test_missing_manifest_returns_none(self):
        with patch.object(spa, "MANIFEST_PATH", Path(self._tmp.name) / "no-such-file.json"):
            self.assertIsNone(spa.entry_assets("whatsnew"))


class RenderSpaPageTest(unittest.TestCase):
    """render.render_spa_page builds the same chrome as the other shelled
    manage pages (sidebar + BASE/SHELL/CHART css) around a bare #root, plus
    the entry's hashed <link>/<script> tags from spa.entry_assets."""

    def test_page_has_sidebar_root_and_entry_tags(self):
        fake_assets = {
            "js": "/assets/app/assets/whatsnew-BZ53WYBJ.js",
            "css": ["/assets/app/assets/whatsnew-BRwL1u47.css"],
        }
        with patch.object(spa, "entry_assets", return_value=fake_assets):
            html = render.render_spa_page("whatsnew", "changelog", "What's new")

        self.assertIn(shell.sidebar_html("changelog"), html)
        self.assertIn('<div id="root">', html)
        self.assertIn(
            '<script type="module" src="/assets/app/assets/whatsnew-BZ53WYBJ.js">',
            html)
        self.assertIn(
            '<link rel="stylesheet" href="/assets/app/assets/whatsnew-BRwL1u47.css">',
            html)
        self.assertIn("What&#x27;s new — Constructor Insight</title>", html)

    def test_unbuilt_frontend_degrades_without_broken_script_tag(self):
        with patch.object(spa, "entry_assets", return_value=None):
            html = render.render_spa_page("whatsnew", "changelog", "What's new")

        self.assertIn(shell.sidebar_html("changelog"), html)
        self.assertIn('<div id="root">', html)
        self.assertNotIn("<script type=\"module\"", html)
        self.assertIn("npm run build", html)


class AssetAppEndpointTest(unittest.TestCase):
    """Same ThreadingHTTPServer + isolated REPORT_DB harness as the existing
    /assets/vega/ route tests (see RetiredVegaRouteTest in
    tests/test_dashboards.py). Uses the real frontend/ build output under
    assets/app/ when present, else a temp fixture with server.ROOT patched."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._env = patch.dict(os.environ, {"REPORT_DB": str(Path(self._tmp.name) / "t.db")})
        self._env.start()

        import server
        self._server = server

        real_app_dir = server.ROOT / "assets" / "app"
        built = sorted(real_app_dir.glob("assets/*.js")) if real_app_dir.is_dir() else []
        self._root_patch = None
        if built:
            self._asset_rel = "assets/" + built[0].name
        else:
            fixture_root = Path(self._tmp.name) / "fixture-root"
            asset_dir = fixture_root / "assets" / "app" / "assets"
            asset_dir.mkdir(parents=True)
            (asset_dir / "whatsnew-FIXTURE.js").write_text("export default 1;\n", encoding="utf-8")
            self._root_patch = patch.object(server, "ROOT", fixture_root)
            self._root_patch.start()
            self._asset_rel = "assets/whatsnew-FIXTURE.js"

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        if self._root_patch is not None:
            self._root_patch.stop()
        self._env.stop()
        self._tmp.cleanup()

    def _get(self, path):
        return urllib.request.urlopen(self.base + path)

    def test_built_asset_served_with_js_mime_and_immutable_cache(self):
        resp = self._get("/assets/app/" + self._asset_rel)
        self.assertEqual(resp.status, 200)
        self.assertIn("javascript", resp.headers.get("Content-Type", ""))
        self.assertIn("immutable", resp.headers.get("Cache-Control", ""))

    def test_manifest_is_not_immutable_cached(self):
        # exercised against the fixture path directly, regardless of whether
        # a real manifest.json exists, by writing one next to the asset dir.
        manifest_dir = (self._server.ROOT / "assets" / "app" / ".vite")
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / "manifest.json"
        created = not manifest_path.exists()
        if created:
            manifest_path.write_text("{}", encoding="utf-8")
        try:
            resp = self._get("/assets/app/.vite/manifest.json")
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Cache-Control"), "no-cache")
        finally:
            if created:
                manifest_path.unlink()

    def test_traversal_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/assets/app/../../server.py")
        self.assertEqual(cm.exception.code, 404)

    def test_missing_file_404s(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/assets/app/assets/does-not-exist.js")
        self.assertEqual(cm.exception.code, 404)


if __name__ == "__main__":
    unittest.main()


class FilterIslandTest(unittest.TestCase):
    """The filter bar's options ride in a `#filter-model` island so the bar can paint
    with the shell instead of behind a skeleton until /api/report/* answers (see
    render.filter_model and frontend/src/hooks/useFilterModel.ts).

    What must hold: the island carries ONLY what does not depend on the request, and
    the page still renders without it."""

    _ASSETS = {"js": "/assets/app/assets/flow-X.js", "css": []}

    def _page(self, filter_inputs):
        with patch.object(spa, "entry_assets", return_value=self._ASSETS):
            return render.render_spa_page("flow", "flow", "Flow", report_chrome=True,
                                          filter_inputs=filter_inputs)

    def test_island_holds_the_query_independent_inputs_only(self):
        fm = render.filter_model({"window_labels": ["7d", "all"], "all_label": "All-time",
                                  "scope_targets": {"repo": ["a"]},
                                  # request-dependent: must NOT reach the island
                                  "scope": "repo:a",
                                  "period": {"preset": "7d", "label": "7 days",
                                             "from": None, "to": None}})
        html = self._page({"periodPresets": fm["periodPresets"],
                           "scopeTargets": fm["scopeTargets"]})
        body = html.split('<script id="filter-model" type="application/json">')[1]
        island = json.loads(body.split("</script>")[0])
        self.assertEqual(sorted(island), ["periodPresets", "scopeTargets"])
        self.assertEqual(island["periodPresets"][-1], {"key": "all", "label": "All-time"})
        self.assertEqual(island["scopeTargets"], {"repo": ["a"]})

    def test_no_island_when_there_is_no_model_yet(self):
        # _filter_inputs returns None on a fresh install; the page must still render
        # (the client then falls back to its skeleton strip).
        html = self._page(None)
        self.assertNotIn('id="filter-model"', html)
        self.assertIn('<div id="root">', html)

    def test_island_cannot_close_its_own_script_tag(self):
        html = self._page({"scopeTargets": {"repo": ["</script><script>x=1</script>"]}})
        self.assertNotIn("</script><script>x=1", html)
        self.assertIn("<\\/script>", html)


class NavCarryTest(unittest.TestCase):
    """shell.zone_carry decides which report-query params a nav link keeps. Both
    renderers consult it — the server here, components/Sidebar.tsx via the `carry`
    list in the nav model — so this pins the rule itself."""

    _QUERY = {"p": "30d", "slice": "repo:a", "person": "ainetx", "from": "", "to": ""}

    def test_manage_links_carry_nothing(self):
        self.assertIsNone(shell.zone_carry("manage", self._QUERY))

    def test_global_filters_carry_into_every_report_zone(self):
        for zone in ("overview", "development", "person", "people", "ai"):
            kept = shell.zone_carry(zone, self._QUERY)
            self.assertEqual(kept.get("p"), "30d", zone)
            self.assertEqual(kept.get("slice"), "repo:a", zone)

    def test_the_subject_carries_only_inside_its_own_zone(self):
        self.assertEqual(shell.zone_carry("person", self._QUERY).get("person"), "ainetx")
        for zone in ("overview", "development", "people", "ai"):
            self.assertNotIn("person", shell.zone_carry(zone, self._QUERY), zone)

    def test_person_view_links_keep_whose_page_it_is(self):
        # The bug this rule exists for: switching Person views dropped the person.
        # report_caption() opens the store, and store.connect() creates the DB lazily,
        # so an unpinned caption would let these touch (or make) a real database for a
        # string no assertion here looks at.
        with patch.object(shell, "report_caption", return_value=""):
            html = shell.sidebar_html("person-overview", {"person": "ainetx", "p": "30d"})
        for view in ("activity", "work", "impact", "score"):
            self.assertIn(f'href="/person?view={view}&p=30d&person=ainetx"', html)

    def test_the_key_list_is_derived_not_restated(self):
        # server.py reads exactly these off the request; if the list it reads and the
        # rule that carries them ever disagreed, a param would be read and dropped
        # (or carried and never read) with nothing failing.
        self.assertEqual(set(shell.CARRY_KEYS),
                         set(shell.CARRY_GLOBAL) | set(shell.CARRY_SUBJECT))

    def test_model_advertises_the_same_rule_it_applied(self):
        # The client merges the live query using the zone's `carry` list; if that list
        # disagreed with what the server merged, the two renderers would produce
        # different links for the same page.
        with patch.object(shell, "report_caption", return_value=""):
            model = json.loads(shell.nav_model_json("flow", self._QUERY))
        for zone in model["zones"]:
            allowed = set(zone["carry"])
            applied = set(shell.zone_carry(zone["key"], self._QUERY) or {})
            self.assertTrue(applied <= allowed, zone["key"])
            for key in allowed:
                self.assertIn(key, shell.CARRY_KEYS, zone["key"])


class FilterInputsCostTest(unittest.TestCase):
    """The island must not put a model BUILD on the page path.

    load_data() + build_model() measured 3.5s against production. _filter_inputs peeks
    the cache instead, so a cold page costs one skeleton strip rather than a shell that
    blocks for seconds — the opposite of what the island is for."""

    def test_page_path_peeks_the_cache_and_never_builds(self):
        import server
        with patch.object(server, "_report_model") as build, \
             patch.object(server, "_cached_report_model", return_value=None) as peek:
            got = server.Handler._filter_inputs(object.__new__(server.Handler))
        self.assertIsNone(got, "no cached model → no island, and the page still renders")
        peek.assert_called_once()
        build.assert_not_called()

    def test_a_cached_model_becomes_the_island(self):
        import server
        model = {"window_labels": ["30d", "all"], "all_label": "All-time",
                 "scope_targets": {"element": ["studio"]}}
        with patch.object(server, "_report_model") as build, \
             patch.object(server, "_cached_report_model", return_value=model):
            got = server.Handler._filter_inputs(object.__new__(server.Handler))
        self.assertEqual(sorted(got), ["periodPresets", "scopeTargets"])
        self.assertEqual(got["scopeTargets"], {"element": ["studio"]})
        build.assert_not_called()


class CaptionEscapingTest(unittest.TestCase):
    """`report_caption()` is `runs.org` straight from the DB, and it lands in the navbar
    title and the brand block on EVERY page. An org set through /api/setup/save is
    regex-validated, but one arriving from a config file or the environment is not, so
    the sidebar escapes it rather than trusting where it came from. The React twin gets
    it as JSON and escapes on output, so this is the only unguarded path."""

    def test_markup_in_the_org_name_cannot_reach_the_page(self):
        evil = '</span><script>alert(1)</script>'
        with patch.object(shell, "report_caption", return_value=evil):
            html = shell.sidebar_html("overview")
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn("&lt;script&gt;alert(1)", html)

    def test_a_plain_caption_still_renders(self):
        with patch.object(shell, "report_caption", return_value="constructorfabric · 29 Jul"):
            html = shell.sidebar_html("overview")
        self.assertIn("constructorfabric · 29 Jul", html)

    def test_the_fallback_tagline_survives_an_empty_caption(self):
        with patch.object(shell, "report_caption", return_value=""):
            html = shell.sidebar_html("overview")
        self.assertIn("Contribution &amp; Usage", html)


class CarryHrefTest(unittest.TestCase):
    """_carry_href merges the request's params into a nav href, and the href wins where
    it already says something — mirroring Sidebar.tsx's `!params.has(k)`. The two
    renderers emitting different links for the same page is the failure this module is
    built around."""

    def test_the_href_wins_over_the_carry(self):
        out = shell._carry_href("/person?view=work&person=bob", {"person": "alice"})
        self.assertEqual(out.count("person="), 1)
        self.assertIn("person=bob", out)

    def test_a_carry_key_the_href_omits_is_appended(self):
        out = shell._carry_href("/person?view=work", {"person": "alice", "p": "30d"})
        self.assertIn("view=work", out)
        self.assertIn("person=alice", out)
        self.assertIn("p=30d", out)


class NoChartRuntimeTest(unittest.TestCase):
    """Vega-Lite is gone: every chart is Recharts, inside the page's own bundle.

    What stood here guarded WHICH routes carried the 808KB runtime. There is no
    runtime to carry now, so what is worth guarding is that nothing brings one back —
    a charting library in a <script src>, or a route asking for one."""

    def test_no_page_loads_a_chart_runtime_from_the_head(self):
        with patch.object(spa, "entry_assets", return_value={"js": "/x.js", "css": []}):
            html = render.render_spa_page("overview", "overview", "Overview",
                                          report_chrome=True)
        self.assertNotIn("/assets/vega/", html)
        self.assertNotIn("vega-lite", html)

    def test_render_spa_page_has_no_vega_switch_left(self):
        import inspect
        self.assertNotIn("vega", inspect.signature(render.render_spa_page).parameters)

    def test_the_retired_spec_builder_and_its_bundles_are_gone(self):
        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "backend/vega_spec.py").exists())
        self.assertFalse((root / "assets/vega").exists())
        for name in ("render.py", "dashboards.py", "server.py", "shell.py"):
            self.assertNotIn("import vega_spec", (root / "backend" / name).read_text(), name)

