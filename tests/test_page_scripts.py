"""Syntax-check the JavaScript embedded in server-rendered pages.

These pages are built as big Python f-strings with hand-doubled braces. A stray
`\\n` (which the f-string turns into a REAL newline inside a quoted JS string) or a
mis-doubled brace produces valid Python and plausible-looking HTML but BROKEN
JavaScript — which no string-level assertion catches. We extract every inline
<script> and run it through `node --check`, the same parse the browser does.

Skips cleanly if node isn't installed (so it never blocks a node-less CI), and
skips a page that can't render without external state rather than failing.
"""
import re
import shutil
import subprocess
import tempfile
import unittest

NODE = shutil.which("node")
_SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)


def _scripts(html: str):
    # skip <script src=...> (no inline body) and empty blocks
    return [s for s in _SCRIPT_RE.findall(html) if s.strip()]


@unittest.skipUnless(NODE, "node not installed — cannot syntax-check inline JS")
class PageScriptSyntaxTest(unittest.TestCase):
    def _check_page(self, html: str, label: str):
        scripts = _scripts(html)
        self.assertTrue(scripts, f"{label}: expected at least one inline <script>")
        for i, js in enumerate(scripts):
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=True) as fh:
                fh.write(js)
                fh.flush()
                r = subprocess.run([NODE, "--check", fh.name],
                                   capture_output=True, text=True)
            self.assertEqual(r.returncode, 0,
                             f"{label}: inline <script> #{i} is not valid JS:\n{r.stderr}")

    def test_portal_html_js_is_valid(self):
        import server
        try:
            html = server.portal_html().decode()
        except Exception as exc:                       # noqa: BLE001 — needs a store
            self.skipTest(f"portal_html could not render here: {exc}")
        self._check_page(html, "portal_html (/update)")

    def test_setup_html_js_is_valid(self):
        import server
        try:
            html = server.setup_html().decode()
        except Exception as exc:                       # noqa: BLE001
            self.skipTest(f"setup_html could not render here: {exc}")
        self._check_page(html, "setup_html (/setup)")

    def test_taxonomy_wizard_js_is_valid(self):
        import os
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        import semantic_editor
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                html = semantic_editor.render_wizard_page()
        self._check_page(html, "taxonomy wizard (/semantic)")

    def test_config_editor_js_is_valid(self):
        import os
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        import configstore
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                html = configstore.render_page()
        self._check_page(html, "config editor (/config)")

    def test_metrics_catalog_js_is_valid(self):
        import metrics_catalog
        self._check_page(metrics_catalog.render_page(), "metrics catalog (/metrics)")

    def test_identity_editor_js_is_valid(self):
        import directory
        try:
            html = directory.render_page()
        except Exception as exc:                       # noqa: BLE001 — needs collected data
            self.skipTest(f"identity editor could not render here: {exc}")
        self._check_page(html, "identity editor (/identity)")

    def test_calibrate_js_is_valid(self):
        import os
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        import calibrate
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                html = calibrate.render_page("me@example.com")
        self._check_page(html, "calibrate (/calibrate)")


if __name__ == "__main__":
    unittest.main()
