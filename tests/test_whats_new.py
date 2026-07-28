import json
import os
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import changelog


class ReleasesAccessorTest(unittest.TestCase):
    """changelog.releases() exposes the module-level CHANGELOG as a plain
    structure — the data source for both render_page() (HTML, transition
    fallback) and /api/whats-new (JSON, the React pilot's data source)."""

    def test_returns_the_changelog_list(self):
        self.assertIs(changelog.releases(), changelog.CHANGELOG)

    def test_shape_is_date_and_changes(self):
        releases = changelog.releases()
        self.assertGreater(len(releases), 0)
        for rel in releases:
            self.assertIn("date", rel)
            self.assertIn("changes", rel)
            for change in rel["changes"]:
                self.assertIn("type", change)
                self.assertIn("title", change)
                self.assertIn("detail", change)


class WhatsNewApiEndpointTest(unittest.TestCase):
    """Same ThreadingHTTPServer + isolated REPORT_DB harness as
    VegaAssetEndpointTest/AssetAppEndpointTest (see tests/test_dashboards.py,
    tests/test_spa.py) — GET /api/whats-new needs no DB, so an isolated
    (empty) REPORT_DB just keeps the test from touching the real one."""

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

    def _get_json(self, path):
        with urllib.request.urlopen(self.base + path) as resp:
            return resp.status, json.loads(resp.read())

    def test_ok_and_releases_shape(self):
        status, body = self._get_json("/api/whats-new")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIsInstance(body["releases"], list)
        self.assertGreater(len(body["releases"]), 0)
        first = body["releases"][0]
        self.assertIn("date", first)
        self.assertIsInstance(first["changes"], list)
        change = first["changes"][0]
        self.assertIn("type", change)
        self.assertIn("title", change)
        self.assertIn("detail", change)

    def test_matches_changelog_module_data(self):
        import changelog
        _, body = self._get_json("/api/whats-new")
        self.assertEqual(body["releases"], changelog.CHANGELOG)


if __name__ == "__main__":
    unittest.main()
