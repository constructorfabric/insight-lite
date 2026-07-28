"""Drift guard for the visual-component catalog (view_registry).

Mirrors the metrics-registry completeness test: every registered view must point
at a component that actually exists (a Jinja macro in templates/, or a render.py
chart function), and must carry the metadata a catalog consumer (the /views page
and the MCP views_catalog tool) needs. Keeps the catalog honest as components move.
"""
import os
import sys
import unittest

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_root, "backend"))   # modules live in backend/
sys.path.insert(0, _root)

import view_registry as vr


class ViewRegistryTest(unittest.TestCase):
    def test_catalog_is_non_trivial(self):
        self.assertGreaterEqual(len(vr.all_views()), 6)

    def test_every_view_resolves_to_a_real_component(self):
        """No stale entry: each view's ref must point at an existing macro/function."""
        broken = []
        for v in vr.all_views():
            r = vr.resolve_ref(v["ref"])
            if not r["ok"]:
                broken.append(f"{v['name']} -> {v['ref']} ({r['why']})")
        self.assertEqual(broken, [], f"views pointing at missing components: {broken}")

    def test_every_view_has_the_catalog_metadata(self):
        """Each entry must be usable by a consumer: purpose, when-to-use, an example,
        a known group, and at least one param."""
        groups = {g for g, _ in vr.GROUPS}
        for v in vr.all_views():
            for field in ("purpose", "when_to_use", "example"):
                self.assertTrue(v.get(field), f"{v['name']}: empty {field}")
            self.assertIn(v["group"], groups, f"{v['name']}: unknown group {v['group']}")
            self.assertTrue(v["params"], f"{v['name']}: no params documented")
            for p in v["params"]:
                self.assertIn("name", p); self.assertIn("type", p); self.assertIn("desc", p)

    def test_kpi_tile_is_registered(self):
        """The component we standardised on must be in the catalog."""
        self.assertIn("kpi_tile", vr.names())


if __name__ == "__main__":
    unittest.main()
