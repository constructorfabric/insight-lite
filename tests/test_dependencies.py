"""requirements.txt must pin majors.

This exists because of a real break, not a style preference. `mcp>=1.2` with no upper
bound meant the image contents depended on the day it was built: mcp 2.0 removed
mcp.server.fastmcp, and the first rebuild after that release published an MCP service
that crash-looped on import — from a commit whose test suite was green.

Nothing in the repo changed. That is the property worth defending: a rebuild of an
unchanged commit should produce an equivalent image. Raising a major is then a
deliberate edit, reviewable alongside whatever code migration it needs.
"""
import re
import unittest
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"

# name, then any number of comma-separated version constraints
_SPEC = re.compile(r"^([A-Za-z0-9._-]+)\s*(.*)$")


def _requirements():
    """(line number, name, constraint text) for each real requirement."""
    out = []
    for n, raw in enumerate(REQUIREMENTS.read_text().splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = _SPEC.match(line)
        assert m, f"{REQUIREMENTS.name}:{n} is not a requirement line: {raw!r}"
        out.append((n, m.group(1), m.group(2).strip()))
    return out


class PinsTest(unittest.TestCase):
    def test_the_file_parses_into_requirements(self):
        """Guards the test itself: a parser that silently matches nothing would make
        every assertion below vacuously pass."""
        self.assertGreaterEqual(len(_requirements()), 5)

    def test_every_dependency_has_a_lower_bound(self):
        """Without one, an old resolver can pick a version predating a feature we use."""
        for n, name, spec in _requirements():
            self.assertRegex(spec, r"(>=|==|~=)",
                             f"{name} (line {n}) has no minimum version")

    def test_every_dependency_has_an_upper_bound(self):
        """The mcp 2.0 failure. `==` and `~=` bound on their own; anything else needs
        an explicit `<`."""
        for n, name, spec in _requirements():
            bounded = "<" in spec or spec.startswith("==") or spec.startswith("~=")
            self.assertTrue(bounded,
                            f"{name} (line {n}) is unbounded ({spec!r}) — a rebuild "
                            f"can pull a new major and change the image. Add `,<N`.")

    def test_installed_versions_satisfy_the_file(self):
        """Catches the local dev env drifting from the pins — the state in which the
        suite passes on a machine whose libraries the image will not have."""
        try:
            from importlib.metadata import PackageNotFoundError, version
        except ImportError:                             # pragma: no cover - py<3.8
            self.skipTest("importlib.metadata unavailable")
        for n, name, spec in _requirements():
            try:
                have = version(name)
            except PackageNotFoundError:
                continue                                # optional at test time
            upper = re.search(r"<\s*(\d+)", spec)
            if not upper:
                continue
            major = int(re.match(r"(\d+)", have).group(1))
            self.assertLess(major, int(upper.group(1)),
                            f"{name} {have} is installed but requirements.txt says "
                            f"{spec} — the tests are not running against what ships")


if __name__ == "__main__":
    unittest.main()
