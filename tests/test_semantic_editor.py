import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import semantic
import semantic_editor
import store


def _seed_repos(conn):
    store.write_repos_dim(conn, [
        {"key": "o/a", "org": "o", "name": "a", "element": "Alpha"},
        {"key": "o/b", "org": "o", "name": "b", "element": "Beta"}])


class ScopedSaveTest(unittest.TestCase):
    def test_same_label_different_category_per_element(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                _seed_repos(conn)
                # global: spec -> docs
                semantic_editor.save(conn, "global", "",
                    {"categories": {"labels": {"spec": "docs"}, "types": {}},
                     "stages": {"statuses": {}}, "ci": {"roles": {}}})
                # element Alpha overrides spec -> story
                semantic_editor.save(conn, "element", "Alpha",
                    {"categories": {"labels": {"spec": "story"}, "types": {}},
                     "stages": {"statuses": {}}, "ci": {"roles": {}}})
                layers = semantic.load_layers(conn)
                alpha = semantic.resolve(layers, {"org": "o", "element": "Alpha"})["config"]
                beta = semantic.resolve(layers, {"org": "o", "element": "Beta"})["config"]
                self.assertEqual(alpha["categories"]["labels"]["spec"], "story")   # overridden
                self.assertEqual(beta["categories"]["labels"]["spec"], "docs")     # inherited global

    def test_clearing_override_deletes_the_scope_row(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                _seed_repos(conn)
                semantic_editor.save(conn, "element", "Alpha",
                    {"categories": {"labels": {"x": "bug"}, "types": {}},
                     "stages": {"statuses": {}}, "ci": {"roles": {}}})
                self.assertIn("element:Alpha:categories", store.read_overrides(conn, "semantic"))
                # save again with the item cleared -> the empty scope row is removed
                semantic_editor.save(conn, "element", "Alpha",
                    {"categories": {"labels": {"x": ""}, "types": {}},
                     "stages": {"statuses": {}}, "ci": {"roles": {}}})
                self.assertNotIn("element:Alpha:categories", store.read_overrides(conn, "semantic"))

    def test_scope_data_shows_inherited(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                _seed_repos(conn)
                store.write_issues(conn, [{"repo": "o/a", "number": 1, "org": "o",
                    "author_login": "x", "created_at": "t", "is_bug": 0, "is_feature": 0,
                    "is_migration": 0, "is_bot": 0, "issue_type": "", "labels": ["spec"]}])
                semantic_editor.save(conn, "global", "",
                    {"categories": {"labels": {"spec": "docs"}, "types": {}},
                     "stages": {"statuses": {}}, "ci": {"roles": {}}})
                d = semantic_editor.scope_data(conn, "element", "Alpha")
                # Alpha has no own override yet, but inherits spec->docs from global
                self.assertEqual(d["own"]["categories"]["labels"], {})
                self.assertEqual(d["inherited"]["categories"]["labels"]["spec"], "docs")
                self.assertEqual([l["name"] for l in d["scan"]["labels"]], ["spec"])


class MigrateBucketsTest(unittest.TestCase):
    def test_remaps_old_vocabulary_and_is_idempotent(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                _seed_repos(conn)
                # write a patch straight to the store using the OLD vocabulary
                store.write_override(conn, "semantic", "global::categories",
                    {"labels": {"a": "story", "b": "chore", "c": "bug"},
                     "types": {"Task": "chore"}})
                store.write_override(conn, "semantic", "element:Alpha:stages",
                    {"statuses": {"Grooming": "spec", "Doing": "in_progress"}})

                n = semantic_editor.migrate_saved_buckets(conn)
                self.assertGreaterEqual(n, 2)
                ov = store.read_overrides(conn, "semantic")
                cats = ov["global::categories"]
                self.assertEqual(cats["labels"], {"a": "feature", "b": "task", "c": "bug"})
                self.assertEqual(cats["types"], {"Task": "task"})
                # global patch also gains the current settings (stage order lives on stages)
                self.assertEqual(ov["element:Alpha:stages"]["statuses"],
                                 {"Grooming": "ready", "Doing": "in_progress"})
                # running again changes nothing
                self.assertEqual(semantic_editor.migrate_saved_buckets(conn), 0)


class WizardDataTest(unittest.TestCase):
    def test_triage_pipeline_and_coverage(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                _seed_repos(conn)
                n = 0

                def mk(labels, itype=""):
                    nonlocal n
                    n += 1
                    return {"repo": "o/a", "number": n, "org": "o", "author_login": "x",
                            "created_at": "t", "is_bug": 0, "is_feature": 0,
                            "is_migration": 0, "is_bot": 0, "issue_type": itype, "labels": labels}

                issues = [mk(["bug"], "Bug"), mk(["bug"], "Bug")]      # known -> auto + covered
                issues += [mk(["widget"]) for _ in range(5)]           # unknown, high volume -> decide
                issues += [mk(["zzz"])]                                # unknown, rare -> tail
                store.write_issues(conn, issues)

                with patch.object(semantic_editor, "_TAIL_MAX", 3):
                    d = semantic_editor.wizard_data(conn, "global", "")

                self.assertEqual(d["buckets"]["categories"],
                                 ["bug", "feature", "task", "epic", "spec", "docs", "test"])
                self.assertEqual([l["key"] for l in d["stages"]["lanes"]],
                                 ["backlog", "ready", "in_progress", "review", "qa", "done", "released"])
                types = {t["name"]: t for t in d["categories"]["types"]}
                self.assertEqual(types["Bug"]["current"], "bug")
                self.assertIn("bug", {r["name"] for r in d["categories"]["auto"]})
                self.assertIn("widget", {r["name"] for r in d["categories"]["decide"]})
                self.assertIn("zzz", {r["name"] for r in d["categories"]["tail"]})
                cov = d["categories"]["coverage"]
                self.assertEqual((cov["total"], cov["covered"]), (8, 2))

                # coverage_preview computes exact coverage for the posted config only
                prev = semantic_editor.coverage_preview(conn, "global", "",
                    {"categories": {"labels": {"widget": "feature", "bug": "bug"}, "types": {}}})
                self.assertEqual((prev["total"], prev["covered"]), (8, 7))  # 2 bug + 5 widget


class WizardInheritanceTest(unittest.TestCase):
    def test_own_vs_inherited_flags_drive_delta_save(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                _seed_repos(conn)
                store.write_issues(conn, [{"repo": "o/a", "number": 1, "org": "o",
                    "author_login": "x", "created_at": "t", "is_bug": 0, "is_feature": 0,
                    "is_migration": 0, "is_bot": 0, "issue_type": "", "labels": ["spec", "bug"]}])
                semantic_editor.save(conn, "global", "",
                    {"categories": {"labels": {"spec": "docs", "bug": "bug"}, "types": {}},
                     "stages": {"statuses": {}}, "ci": {"roles": {}}})
                semantic_editor.save(conn, "element", "Alpha",
                    {"categories": {"labels": {"spec": "test"}, "types": {}},
                     "stages": {"statuses": {}}, "ci": {"roles": {}}})
                d = semantic_editor.wizard_data(conn, "element", "Alpha")
                by = {l["name"]: l for l in d["categories"]["auto"]}
                # spec is overridden HERE → own=True (the wizard must re-send it on save)
                self.assertEqual(by["spec"]["current"], "test")
                self.assertTrue(by["spec"]["own"])
                # bug is only inherited from global → own=False (must NOT be pinned here)
                self.assertEqual(by["bug"]["current"], "bug")
                self.assertFalse(by["bug"]["own"])
                self.assertTrue(by["bug"]["inherited"])


class EffectiveDataTest(unittest.TestCase):
    def test_provenance_marks_the_winning_scope(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                _seed_repos(conn)
                semantic_editor.save(conn, "global", "",
                    {"categories": {"labels": {"spec": "docs", "bug": "bug"}, "types": {}},
                     "stages": {"statuses": {}}, "ci": {"roles": {}}})
                semantic_editor.save(conn, "element", "Alpha",
                    {"categories": {"labels": {"spec": "test"}, "types": {}},
                     "stages": {"statuses": {}}, "ci": {"roles": {}}})
                eff = semantic_editor.effective_data(conn, "element", "Alpha")
                by = {i["name"]: i for i in eff["categories"]["labels"]}
                self.assertEqual(by["spec"]["bucket"], "test")
                self.assertEqual(by["spec"]["from"], "element")   # overridden here
                self.assertEqual(by["bug"]["bucket"], "bug")
                self.assertEqual(by["bug"]["from"], "global")     # inherited
                self.assertEqual([c["level"] for c in eff["chain"]],
                                 ["global", "org", "element"])


if __name__ == "__main__":
    unittest.main()
