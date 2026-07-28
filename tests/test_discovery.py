import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import discovery
import store


class SuggestHeuristicsTest(unittest.TestCase):
    def test_categories_item_to_bucket(self):
        scanned = {
            "issue_types": [{"name": "Bug", "count": 5}, {"name": "Feature", "count": 3},
                            {"name": "Task", "count": 2}],
            "labels": [{"name": "bug", "count": 9}, {"name": "high-complexity", "count": 4},
                       {"name": "documentation", "count": 2}],
            "statuses": [], "workflows": [],
        }
        r = discovery.suggest(scanned)
        cats = r["config"]["categories"]
        self.assertEqual(cats["types"], {"Bug": "bug", "Feature": "feature", "Task": "task"})
        self.assertEqual(cats["labels"]["bug"], "bug")
        self.assertEqual(cats["labels"]["documentation"], "docs")
        self.assertNotIn("high-complexity", cats["labels"])          # unrecognised → unmapped
        self.assertIn("high-complexity", r["unmapped"]["labels"])

    def test_stage_mapping_percentages_and_case(self):
        scanned = {"issue_types": [], "labels": [], "workflows": [], "statuses": [
            {"name": "Todo", "count": 1}, {"name": "In progress", "count": 1},
            {"name": "In Progress", "count": 1}, {"name": "In Review", "count": 1},
            {"name": "Done", "count": 1}, {"name": "50%", "count": 1},
            {"name": "100%", "count": 1}, {"name": "Zzz", "count": 1}]}
        st = discovery.suggest(scanned)["config"]["stages"]["statuses"]
        self.assertEqual(st["Todo"], "ready")            # "to do" is the ready column
        self.assertEqual(st["In progress"], "in_progress")
        self.assertEqual(st["In Progress"], "in_progress")
        self.assertEqual(st["50%"], "in_progress")
        self.assertEqual(st["100%"], "done")
        self.assertEqual(st["In Review"], "review")
        self.assertNotIn("Zzz", st)

    def test_full_lifecycle_stages(self):
        scanned = {"issue_types": [], "labels": [], "workflows": [], "statuses": [
            {"name": "Backlog", "count": 1}, {"name": "Ready", "count": 1},
            {"name": "QA", "count": 1}, {"name": "Deployed", "count": 1},
            {"name": "Released", "count": 1}]}
        st = discovery.suggest(scanned)["config"]["stages"]["statuses"]
        self.assertEqual(st["Backlog"], "backlog")
        self.assertEqual(st["Ready"], "ready")
        self.assertEqual(st["QA"], "qa")
        self.assertEqual(st["Deployed"], "released")
        self.assertEqual(st["Released"], "released")

    def test_ci_roles(self):
        scanned = {"issue_types": [], "labels": [], "statuses": [], "workflows": [
            {"name": "CI", "count": 1}, {"name": "e2e-nightly", "count": 1},
            {"name": "release-plz", "count": 1}, {"name": "Cache Cleanup", "count": 1},
            {"name": "Mystery", "count": 1}]}
        roles = discovery.suggest(scanned)["config"]["ci"]["roles"]
        self.assertEqual(roles["CI"], "gate")
        self.assertEqual(roles["e2e-nightly"], "nightly")
        self.assertEqual(roles["release-plz"], "release")
        self.assertEqual(roles["Cache Cleanup"], "ignore")
        self.assertNotIn("Mystery", roles)


class ScopeAwareScanTest(unittest.TestCase):
    def _seed(self, conn):
        store.write_issues(conn, [
            {"repo": "o/a", "number": 1, "org": "o", "author_login": "x", "created_at": "t",
             "is_bug": 0, "is_feature": 0, "is_migration": 0, "is_bot": 0,
             "issue_type": "Bug", "labels": ["bug", "team:a"]},
            {"repo": "o/b", "number": 2, "org": "o", "author_login": "x", "created_at": "t",
             "is_bug": 0, "is_feature": 0, "is_migration": 0, "is_bot": 0,
             "issue_type": "", "labels": ["bug", "team:b"]}])
        store.write_repos_dim(conn, [
            {"key": "o/a", "org": "o", "name": "a", "element": "Alpha"},
            {"key": "o/b", "org": "o", "name": "b", "element": "Beta"}])

    def test_scan_scoped_to_repos(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                self._seed(conn)
                allv = {d["name"] for d in discovery.scan(conn)["labels"]}
                self.assertEqual(allv, {"bug", "team:a", "team:b"})
                repos, project = discovery.repos_for_scope(conn, "element", "Alpha")
                self.assertEqual(repos, ["o/a"])
                scoped = {d["name"] for d in discovery.scan(conn, repos, project)["labels"]}
                self.assertEqual(scoped, {"bug", "team:a"})            # Beta's team:b excluded

    def test_scope_targets(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                self._seed(conn)
                t = discovery.scope_targets(conn)
                self.assertEqual(t["org"], ["o"])
                self.assertEqual(sorted(t["element"]), ["Alpha", "Beta"])
                self.assertEqual(sorted(t["repo"]), ["o/a", "o/b"])


if __name__ == "__main__":
    unittest.main()
