"""The visual base is defined once, and the two render paths cannot disagree about it.

The portal renders through two paths — Python/Jinja pages that inline CSS into a
<style> block, and React entries that get it from Vite. The token block and the element
base used to exist twice, copied by hand, with a comment at the top of base.css asking
the next person to keep them reconciled. These tests replace that request with a check.

  GeneratorTest   — the generated artefacts match design/tokens.json + base-elements.css
  ParityTest      — the two paths' assembled CSS is the same, apart from @font-face,
                    which is the one rule they must NOT share (font-display block/swap)
  ImportOrderTest — the @import rules in base.css stay first, or CSS drops them
  ContrastTest    — declared body-text pairs meet WCAG 2.2 AA, with today's failures
                    pinned so a NEW one fails the build

ContrastTest deliberately ships a KNOWN_FAILURES table rather than a green assertion:
eight pairs fail AA today, `--mut` (223 references, the most-used token in the codebase)
worst of all at 2.73 on --panel2. Pinning the measured ratios means the palette cannot
get worse quietly, and fixing one is a matter of deleting its line. See
docs/design-system.md for the plan those fixes belong to.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STYLES = ROOT / "frontend" / "src" / "styles"

sys.path.insert(0, str(ROOT / "tools"))
import gen_tokens                                                    # noqa: E402


def _rules(css: str) -> list[str]:
    """CSS reduced to a comparable rule list: comments dropped, whitespace collapsed.
    Formatting is not the invariant — what the browser applies is."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"\s+", " ", css)
    return sorted(r.strip() + "}" for r in css.split("}") if r.strip())


def _inline_imports(path: Path) -> str:
    """base.css as Vite serves it — @import statements replaced by their targets."""
    css = path.read_text()
    for target in re.findall(r'@import\s+"([^"]+)";', css):
        css = css.replace(f'@import "{target}";', path.parent.joinpath(target).read_text())
    return css


def _font_faces(css: str) -> list[str]:
    return [r for r in _rules(css) if "@font-face" in r]


class GeneratorTest(unittest.TestCase):
    def test_the_committed_artefacts_are_not_stale(self):
        """`python3 tools/gen_tokens.py` produces exactly what is committed. If this
        fails, someone hand-edited a generated file or forgot to re-run the generator."""
        self.assertEqual(gen_tokens.main(["--check"]), 0,
                         "generated token files are stale — run: python3 tools/gen_tokens.py")

    def test_every_css_token_reaches_both_css_outputs(self):
        data = gen_tokens.load()
        import tokens
        css = (STYLES / "tokens.css").read_text()
        theme = data["themes"]["light"]
        for group in gen_tokens.CSS_GROUPS:
            for name, spec in gen_tokens.entries(theme, group).items():
                decl = f"--{name}:{spec['value']};"
                with self.subTest(token=name):
                    self.assertIn(decl, css, f"{name} missing from tokens.css")
                    self.assertIn(decl, tokens.TOKENS_CSS, f"{name} missing from tokens.py")

    def test_every_token_reaches_the_ts_module(self):
        """tokens.ts carries ALL groups, including `score`, which is read only from JS
        (PersonScore picks a band) and would be dead weight in every page's <style>."""
        data = gen_tokens.load()
        ts = (ROOT / "frontend" / "src" / "lib" / "tokens.ts").read_text()
        theme = data["themes"]["light"]
        for group in gen_tokens.GROUPS:
            for name, spec in gen_tokens.entries(theme, group).items():
                with self.subTest(token=name):
                    self.assertIn(f'"{name}": "{spec["value"]}"', ts)

    def test_score_is_deliberately_absent_from_the_css(self):
        """The split is intentional, so it is asserted — otherwise a later reader
        'fixes' it by adding score to CSS_GROUPS and nobody notices the bloat."""
        css = (STYLES / "tokens.css").read_text()
        for name in gen_tokens.entries(gen_tokens.load()["themes"]["light"], "score"):
            self.assertNotIn(f"--{name}:", css)

    def test_notes_never_leak_into_an_artefact(self):
        """`_note` keys document the JSON for whoever reads it; emitting one would
        produce a `--_note:` declaration."""
        for path in (STYLES / "tokens.css", ROOT / "backend" / "tokens.py",
                     ROOT / "frontend" / "src" / "lib" / "tokens.ts"):
            self.assertNotIn("_note", path.read_text(), f"{path.name} leaked a _note key")


class ParityTest(unittest.TestCase):
    """What used to be a hand-kept copy. The whole point of the generator is that this
    cannot drift, so it is asserted rather than trusted."""

    def setUp(self):
        import shell
        self.python_css = shell.BASE_CSS
        self.react_css = _inline_imports(STYLES / "base.css")

    def test_both_paths_apply_the_same_rules_apart_from_the_fonts(self):
        py = [r for r in _rules(self.python_css) if "@font-face" not in r]
        react = [r for r in _rules(self.react_css) if "@font-face" not in r]
        self.assertEqual(py, react)

    def test_the_font_face_divergence_is_still_the_only_one(self):
        """font-display MUST differ: swap on the Python path (the font is inlined as
        base64, so swap is instant), block on React (every route is a full page load,
        and swap flashed the fallback font in the sidebar on every navigation)."""
        self.assertTrue(all("font-display:swap" in r for r in _font_faces(self.python_css)))
        self.assertTrue(all("font-display:block" in r for r in _font_faces(self.react_css)))

    def test_neither_shared_file_defines_a_font_face(self):
        """A @font-face in tokens.css or base-elements.css would force both paths onto
        one font-display value and silently undo the divergence above."""
        for name in ("tokens.css", "base-elements.css"):
            self.assertEqual(_font_faces((STYLES / name).read_text()), [],
                             f"{name} must not define @font-face")

    def test_the_python_side_defines_no_tokens_of_its_own(self):
        """shell.py used to carry its own `:root` block. A custom property DEFINED in
        its CSS strings again would be a second source of truth, which is the bug this
        change removes. References — var(--acc) — are the point and stay allowed."""
        source = (ROOT / "backend" / "shell.py").read_text()
        source = re.sub(r"^\s*#.*$", "", source, flags=re.M)          # drop Python comments
        source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)         # and CSS ones
        defined = [m.group(0) for m in re.finditer(r"--[a-z0-9-]+\s*:", source)
                   if "var(" not in source[max(0, m.start() - 4):m.start()]]
        self.assertEqual(defined, [], f"shell.py defines tokens directly: {defined}")


class ImportOrderTest(unittest.TestCase):
    def test_the_imports_come_before_any_rule(self):
        """CSS ignores an @import that follows another rule, so a declaration added
        above them would drop the tokens and the element base with no error anywhere."""
        css = re.sub(r"/\*.*?\*/", "", (STYLES / "base.css").read_text(), flags=re.S)
        statements = list(re.finditer(r'@import\s+"[^"]+"\s*;', css))
        self.assertTrue(statements, "base.css must import the shared visual base")
        # Everything up to the last @import has to be nothing but the other @imports.
        head = css[:statements[-1].end()]
        for s in statements:
            head = head.replace(s.group(0), "")
        self.assertEqual(head.strip(), "",
                         f"a rule precedes an @import in base.css and will drop it: {head.strip()[:80]!r}")


class NoStrayColoursTest(unittest.TestCase):
    """The whole point of the source file: a colour is declared once, there.

    Without this the consolidation decays the first time someone reaches for a hex in
    a hurry, and nothing complains. Anything genuinely un-tokenisable belongs in an
    ALLOW entry with a reason, not in a stylesheet.
    """

    # (path, matched text) pairs that are deliberately not tokens.
    ALLOW = {
        # Doc examples in the view registry, showing a caller what the markup looks
        # like — inert strings rendered nowhere.
        ("backend/view_registry.py", "#10b981"),
        ("backend/view_registry.py", "#ef4444"),
    }

    HEX = re.compile(r"(?<![&\w])#[0-9a-fA-F]{3,8}\b")

    def _offenders(self, paths, strip_comments):
        out = []
        for path in paths:
            rel = str(path.relative_to(ROOT))
            text = strip_comments(path.read_text())
            for m in self.HEX.finditer(text):
                if (rel, m.group(0)) in self.ALLOW:
                    continue
                line = text[:m.start()].count("\n") + 1
                out.append(f"{rel}:{line} {m.group(0)}")
        return out

    def test_no_colour_literal_in_any_stylesheet(self):
        sheets = [p for p in STYLES.glob("*.css") if p.name != "tokens.css"]
        self.assertEqual(
            self._offenders(sheets, lambda t: re.sub(r"/\*.*?\*/", "", t, flags=re.S)), [])

    def test_no_colour_literal_in_any_component(self):
        src = ROOT / "frontend" / "src"
        files = [p for p in src.rglob("*.ts") if p.name != "tokens.ts"]
        files += list(src.rglob("*.tsx"))
        self.assertEqual(
            self._offenders(files, lambda t: re.sub(r"//[^\n]*|/\*.*?\*/", "", t, flags=re.S)), [])

    def test_no_colour_literal_in_the_backend(self):
        """The backend emits colours inside API payloads, so it holds values rather
        than var() references — but they come from tokens.VALUES, not from literals.
        Three separate copies of the same eight-colour swatch list used to live here.

        Parsed, not regexed, and via `ast` rather than `tokenize`. Two earlier versions
        of this test were vacuous, which is worth recording because both failure modes
        look fine in review:
          * stripping Python comments with `#[^\\n]*` also eats `"#abcdef"` inside a
            string — the very thing being searched for;
          * skipping docstrings by "the previous token ended a statement" also skips
            the second line of an implicitly-concatenated string, so a colour written
            across two adjacent string literals slipped through.
        `ast` has neither problem: comments are absent from the tree, a docstring is a
        precisely identifiable node, and implicit concatenation arrives as one node."""
        import ast

        offenders = []
        for path in sorted((ROOT / "backend").glob("*.py")):
            if path.name == "tokens.py":
                continue
            rel = str(path.relative_to(ROOT))
            tree = ast.parse(path.read_text())
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef, ast.AsyncFunctionDef)):
                    doc = node.body[0] if node.body else None
                    if (isinstance(doc, ast.Expr) and isinstance(doc.value, ast.Constant)
                            and isinstance(doc.value.value, str)):
                        docstrings.add(id(doc.value))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and id(node) not in docstrings):
                    for m in self.HEX.finditer(node.value):
                        if (rel, m.group(0)) not in self.ALLOW:
                            offenders.append(f"{rel}:{node.lineno} {m.group(0)}")
        self.assertEqual(offenders, [])


def _channels(hex_colour: str) -> list[int]:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def flatten(colour: str, backdrop: str) -> str:
    """An #rrggbbaa colour composited over an opaque backdrop.

    Needed because a translucent fill is far lighter than its hex suggests, and
    judging the text on it by the raw hex gives the wrong answer: --exp-bg is
    #f59e0b22, amber at 13%, which over a white card is #fef2de."""
    if len(colour.lstrip("#")) != 8:
        return colour
    fg = _channels(colour)
    alpha = int(colour.lstrip("#")[6:8], 16) / 255
    bg = _channels(backdrop)
    return "#%02x%02x%02x" % tuple(
        round(fg[i] * alpha + bg[i] * (1 - alpha)) for i in range(3))


def _relative_luminance(hex_colour: str) -> float:
    channels = [c / 255 for c in _channels(hex_colour)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(fg: str, bg: str) -> float:
    a, b = _relative_luminance(fg), _relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


class ContrastTest(unittest.TestCase):
    """WCAG 2.2 AA for body text is 4.5:1. Pairs come from each colour's `text_on` list
    in design/tokens.json — that is design information, so it lives with the values."""

    AA_NORMAL = 4.5

    # Empty since 2026-08-03: the eight inherited failures were fixed by darkening
    # --mut, --good, --warn, --bad and --exp-fg to the lowest value that clears 4.5:1,
    # keeping each hue and saturation. An entry here is a debt, not a target — add one
    # only with the measured ratio, and delete it when the colour is fixed.
    KNOWN_FAILURES: dict[tuple[str, str], float] = {}

    # Groups that declare text pairs. `color` holds the semantic base, `status` the
    # badge and pill surfaces.
    PAIR_GROUPS = ("color", "status")

    def _pairs(self):
        theme = gen_tokens.load()["themes"]["light"]
        values = {name: spec["value"]
                  for group in self.PAIR_GROUPS
                  for name, spec in gen_tokens.entries(theme, group).items()}
        panel = values["panel"]
        for group in self.PAIR_GROUPS:
            for fg, spec in gen_tokens.entries(theme, group).items():
                for bg in spec.get("text_on", []):
                    yield fg, bg, contrast_ratio(flatten(spec["value"], panel),
                                                 flatten(values[bg], panel))

    def test_no_new_pair_falls_below_aa(self):
        for fg, bg, ratio in self._pairs():
            with self.subTest(pair=f"{fg} on {bg}"):
                if (fg, bg) in self.KNOWN_FAILURES:
                    continue
                self.assertGreaterEqual(
                    round(ratio, 2), self.AA_NORMAL,
                    f"--{fg} on --{bg} is {ratio:.2f}:1, below AA 4.5:1")

    def test_the_known_failures_have_not_got_worse(self):
        """A pinned ratio that drops means someone darkened the surface or lightened the
        text; one that rises above 4.5 means the debt is paid and the line should go."""
        measured = {(fg, bg): round(r, 2) for fg, bg, r in self._pairs()}
        for pair, pinned in self.KNOWN_FAILURES.items():
            with self.subTest(pair=f"{pair[0]} on {pair[1]}"):
                self.assertIn(pair, measured, f"{pair} no longer exists — drop it")
                now = measured[pair]
                self.assertGreaterEqual(now, pinned, f"{pair} regressed: {now} < {pinned}")
                self.assertLess(now, self.AA_NORMAL,
                                f"{pair} now passes AA at {now} — remove it from KNOWN_FAILURES")

    def test_text_on_a_filled_primary_button_passes(self):
        """The one pair not derived from `text_on`: white label on a filled --acc."""
        data = gen_tokens.load()
        ratio = contrast_ratio(data["on_primary"]["value"],
                               data["themes"]["light"]["color"]["acc"]["value"])
        self.assertGreaterEqual(round(ratio, 2), self.AA_NORMAL)


if __name__ == "__main__":
    unittest.main()
