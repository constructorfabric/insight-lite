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
from pathlib import Path

NODE = shutil.which("node")
_SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)


# `<script type="application/json">` islands (the nav model, a route's bootstrap) are
# DATA, not code — node --check would reject perfectly valid JSON. Matched separately
# so they are skipped rather than silently swept in by the tag regex.
_JSON_RE = re.compile(r'<script\b[^>]*type=["\']application/json["\'][^>]*>.*?</script>',
                      re.S | re.I)


def _scripts(html: str):
    # skip JSON islands, <script src=...> (no inline body) and empty blocks
    return [s for s in _SCRIPT_RE.findall(_JSON_RE.sub("", html)) if s.strip()]


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

    def test_the_sidebar_js_is_valid(self):
        """The one hand-written inline script left. This class used to check seven
        server-rendered pages — the update portal, setup wizard, taxonomy wizard,
        config, metrics, identity and calibrate editors — all of which were replaced by
        React routes and removed with the legacy layer. The sidebar's mobile-drawer
        script is still assembled as a Python string and injected into every page, so
        it is still exactly the failure mode this file exists for: a stray `\\n` or a
        mis-doubled brace yields valid Python and broken JS."""
        import shell
        self._check_page(shell.sidebar_html("overview"), "sidebar (every page)")

    def test_the_spa_shell_js_is_valid(self):
        """The shell every React page is served in. It carries the sidebar script plus
        whatever the shell itself injects, so a broken brace there breaks all 23
        routes at once rather than one page."""
        import render
        try:
            html = render.render_spa_page("overview", "overview", "Overview")
        except Exception as exc:                       # noqa: BLE001 — needs a built bundle
            self.skipTest(f"SPA shell could not render here: {exc}")
        self._check_page(html, "SPA shell (render_spa_page)")


if __name__ == "__main__":
    unittest.main()


class HelpButtonLabellingTest(unittest.TestCase):
    """A "?" button carries a NAME in aria-label and its prose in aria-describedby.

    The prose is three sentences. Announcing it as the label means a screen reader reads
    the whole explanation where it should be reading "help", and there is no way to skip
    it — so the text goes into the DOM once, visually hidden, and the button points at
    it. Static because there is no frontend test runner here; the pattern is easy to
    regress by copying the nearest existing button, which is what this catches."""

    _FILES = ("frontend/src/components/FilterBar.tsx", "frontend/src/pages/Person.tsx")

    def _sources(self):
        root = Path(__file__).resolve().parents[1]
        for rel in self._FILES:
            yield rel, (root / rel).read_text()

    def test_no_help_button_announces_its_prose_as_a_label(self):
        # `aria-label={SOMETHING_HELP}` is the shape this forbids: a constant holding
        # sentences, used where a short name belongs.
        for rel, src in self._sources():
            for match in re.finditer(r"aria-label=\{([A-Za-z_]*HELP)\}", src):
                self.fail(f"{rel}: aria-label={{{match.group(1)}}} — that constant is a "
                          f"description; give the button a name and point "
                          f"aria-describedby at the text")

    def test_every_help_button_has_a_description_to_point_at(self):
        for rel, src in self._sources():
            buttons = src.count('className="legend-help"')
            described = src.count("aria-describedby=")
            self.assertEqual(buttons, described,
                             f"{rel}: {buttons} help buttons but {described} "
                             f"aria-describedby — every one needs its own")
            for match in re.finditer(r'aria-describedby="([\w-]+)"', src):
                self.assertIn(f'id="{match.group(1)}"', src,
                              f"{rel}: aria-describedby points at a missing id")
                self.assertIn("className=\"vh\"", src,
                              f"{rel}: the description must be visually hidden")
