import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import semantic


def _layer(level, target, axis, patch):
    return {"level": level, "target": target, "axis": axis, "patch": patch}


class ChainTest(unittest.TestCase):
    def test_global_only(self):
        self.assertEqual(semantic.applicable_chain({}), [("global", "")])

    def test_repo_entity_includes_all_present(self):
        chain = semantic.applicable_chain(
            {"org": "o", "element": "E", "repo": "o/r", "project": "o/12"})
        self.assertEqual(chain, [("global", ""), ("org", "o"), ("element", "E"),
                                 ("repo", "o/r"), ("project", "o/12")])

    def test_element_entity_skips_repo_and_project(self):
        chain = semantic.applicable_chain({"org": "o", "element": "E"})
        self.assertEqual([lv for lv, _ in chain], ["global", "org", "element"])


class KeyTest(unittest.TestCase):
    def test_roundtrip(self):
        for level, target, axis in [("global", "", "categories"),
                                    ("org", "your-old-org", "categories"),
                                    ("repo", "org/name-rust", "ci"),
                                    ("project", "your-old-org/12", "stages")]:
            key = semantic.make_key(level, target, axis)
            self.assertEqual(semantic.parse_key(key), (level, target, axis))

    def test_global_rejects_target(self):
        with self.assertRaises(ValueError):
            semantic.make_key("global", "x", "categories")

    def test_malformed_and_unknown_level(self):
        self.assertIsNone(semantic.parse_key("no-colons"))
        self.assertIsNone(semantic.parse_key("bogus:t:categories"))


class ResolveTest(unittest.TestCase):
    def test_more_specific_scalar_wins(self):
        layers = [
            _layer("global", "", "profile", {"primary_unit": "issue"}),
            _layer("repo", "o/r", "profile", {"primary_unit": "pull_request"}),
        ]
        r = semantic.resolve(layers, {"org": "o", "repo": "o/r"})
        self.assertEqual(r["config"]["profile"]["primary_unit"], "pull_request")
        self.assertEqual(r["provenance"][("profile", "primary_unit")], ["repo"])

    def test_deep_merge_adds_without_clobbering(self):
        layers = [
            _layer("global", "", "categories", {"defs": {"bug": {"role": "defect"}}}),
            _layer("org", "o", "categories", {"defs": {"story": {"role": "feature"}}}),
        ]
        cfg = semantic.resolve(layers, {"org": "o"})["config"]["categories"]
        self.assertEqual(set(cfg["defs"]), {"bug", "story"})   # global bug survives

    def test_list_replace_by_default(self):
        layers = [
            _layer("global", "", "categories", {"defs": {"bug": {"labels": ["bug"]}}}),
            _layer("repo", "o/r", "categories", {"defs": {"bug": {"labels": ["defect"]}}}),
        ]
        cfg = semantic.resolve(layers, {"repo": "o/r"})["config"]["categories"]
        self.assertEqual(cfg["defs"]["bug"]["labels"], ["defect"])   # replaced, not merged

    def test_plus_prefix_appends_and_dedupes(self):
        layers = [
            _layer("global", "", "categories", {"defs": {"bug": {"labels": ["bug"]}}}),
            _layer("repo", "o/r", "categories", {"defs": {"bug": {"+labels": ["bug", "defect"]}}}),
        ]
        r = semantic.resolve(layers, {"repo": "o/r"})
        self.assertEqual(r["config"]["categories"]["defs"]["bug"]["labels"], ["bug", "defect"])
        self.assertEqual(r["provenance"][("categories", "defs", "bug", "labels")],
                         ["global", "repo"])   # both contributed

    def test_axis_isolation(self):
        layers = [
            _layer("global", "", "categories", {"order": ["bug"]}),
            _layer("repo", "o/r", "ci", {"count_events": ["push"]}),
        ]
        cfg = semantic.resolve(layers, {"repo": "o/r"})["config"]
        self.assertEqual(cfg["categories"]["order"], ["bug"])
        self.assertEqual(cfg["ci"]["count_events"], ["push"])

    def test_inapplicable_scope_is_ignored(self):
        # a repo-scoped patch must NOT apply to a different repo
        layers = [_layer("repo", "o/other", "profile", {"primary_unit": "issue"})]
        cfg = semantic.resolve(layers, {"org": "o", "repo": "o/r"})["config"]
        self.assertEqual(cfg, {})

    def test_element_beats_org_but_repo_beats_element(self):
        layers = [
            _layer("org", "o", "profile", {"primary_unit": "issue"}),
            _layer("element", "E", "profile", {"primary_unit": "pull_request"}),
            _layer("repo", "o/r", "profile", {"primary_unit": "commit"}),
        ]
        ent = {"org": "o", "element": "E", "repo": "o/r"}
        self.assertEqual(semantic.resolve(layers, ent)["config"]["profile"]["primary_unit"],
                         "commit")
        # without the repo layer, element wins over org
        self.assertEqual(semantic.resolve(layers[:2], ent)["config"]["profile"]["primary_unit"],
                         "pull_request")


class LoadLayersDbTest(unittest.TestCase):
    def test_roundtrip_through_override_table(self):
        import store
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                store.write_override(conn, "semantic",
                                     semantic.make_key("global", "", "categories"),
                                     {"defs": {"bug": {"labels": ["bug"]}}})
                store.write_override(conn, "semantic",
                                     semantic.make_key("repo", "o/r", "categories"),
                                     {"defs": {"bug": {"+labels": ["defect"]}}})
                store.write_override(conn, "semantic", "malformed-key", {"x": 1})
                r = semantic.effective_for(conn, {"repo": "o/r"})
                self.assertEqual(r["config"]["categories"]["defs"]["bug"]["labels"],
                                 ["bug", "defect"])   # merged across DB rows, bad key skipped


if __name__ == "__main__":
    unittest.main()
