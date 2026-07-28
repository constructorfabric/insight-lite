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
    /assets/vega/ endpoint tests (see VegaAssetEndpointTest in
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
