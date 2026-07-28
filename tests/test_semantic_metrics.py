import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import semantic
import semantic_editor
import semantic_metrics
import store


class CategorizeIssueTest(unittest.TestCase):
    def test_prefer_source_type_over_label(self):
        resolved = {"categories": {"types": {"Task": "chore"}, "labels": {"bug": "bug"},
                                   "prefer_source": ["issue_type", "label", "title"],
                                   "unmatched": "uncategorized"}}
        # type says chore, label says bug → issue_type wins
        self.assertEqual(semantic.categorize_issue(resolved, ["bug"], "Task"), "chore")
        # no type → falls through to label
        self.assertEqual(semantic.categorize_issue(resolved, ["bug"], ""), "bug")
        # nothing matches → unmatched bucket
        self.assertEqual(semantic.categorize_issue(resolved, ["zzz"], ""), "uncategorized")


class IssueMetricsTest(unittest.TestCase):
    def test_drill_ci_runs_lists_only_gate_runs(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                store.write_repos_dim(conn, [{"key": "o/r", "org": "o", "name": "r"}])
                semantic_editor.save(conn, "global", "",
                    {"categories": {"labels": {}, "types": {}}, "stages": {"statuses": {}},
                     "ci": {"roles": {"Gate": "gate"}}})
                store.write_ci_runs(conn, [
                    {"repo": "o/r", "run_id": 10, "workflow": "Gate", "event": "pull_request",
                     "branch": "b", "status": "completed", "conclusion": "success",
                     "created_at": "2026-07-01T00:00:00Z", "run_started_at": "",
                     "updated_at": "", "duration_s": 90, "head_sha": "s", "actor": "a"},
                    {"repo": "o/r", "run_id": 11, "workflow": "Nightly", "event": "schedule",
                     "branch": "b", "status": "completed", "conclusion": "failure",
                     "created_at": "2026-07-02T00:00:00Z", "run_started_at": "",
                     "updated_at": "", "duration_s": 5, "head_sha": "s", "actor": "a"}])
                d = semantic_metrics.drill_ci_runs(conn, "2026-01-01T00:00:00Z",
                                                   "2026-12-31T00:00:00Z")
                self.assertEqual(d["total"], 1)                       # only the gate run
                self.assertEqual(d["rows"][0]["url"],
                                 "https://github.com/o/r/actions/runs/10")
                self.assertEqual(d["rows"][0]["conclusion"], "success")
                self.assertEqual(d["rows"][0]["duration"], "1m30s")

    def test_category_mix_and_close_rate_per_element(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                store.write_repos_dim(conn, [
                    {"key": "o/g", "org": "o", "name": "g", "element": "Core"},
                    {"key": "o/i", "org": "o", "name": "i", "element": "Insight"}])
                # global: spec -> docs ; Insight overrides spec -> bug
                semantic_editor.save(conn, "global", "",
                    {"categories": {"labels": {"spec": "docs", "bug": "bug"}, "types": {}},
                     "stages": {"statuses": {}}, "ci": {"roles": {}}})
                semantic_editor.save(conn, "element", "Insight",
                    {"categories": {"labels": {"spec": "bug"}, "types": {}},
                     "stages": {"statuses": {}}, "ci": {"roles": {}}})
                store.write_issues(conn, [
                    {"repo": "o/g", "number": 1, "org": "o", "author_login": "a",
                     "created_at": "2026-07-01T00:00:00Z", "is_bug": 0, "is_feature": 0,
                     "is_migration": 0, "is_bot": 0, "issue_type": "", "labels": ["spec"],
                     "closed_at": "2026-07-03T00:00:00Z"},           # Core spec -> docs, closed 2d
                    {"repo": "o/i", "number": 2, "org": "o", "author_login": "a",
                     "created_at": "2026-07-01T00:00:00Z", "is_bug": 0, "is_feature": 0,
                     "is_migration": 0, "is_bot": 0, "issue_type": "", "labels": ["spec"],
                     "closed_at": ""}])                              # Insight spec -> bug, open
                m = semantic_metrics.issue_metrics(conn, "2026-01-01T00:00:00Z",
                                                   "2026-12-31T00:00:00Z")
                self.assertEqual(m["issues_total"], 2)
                self.assertEqual(m["issues_by_category"], {"docs": 1, "bug": 1})  # same label!
                self.assertEqual(m["issues_closed"], 1)
                self.assertEqual(m["issue_close_rate"], 50.0)
                self.assertEqual(m["defect_rate"], 50.0)             # the Insight spec = bug
                self.assertEqual(m["issue_median_time_to_close_days"], 2.0)


class CiMetricsTest(unittest.TestCase):
    def test_only_gate_runs_on_counted_events(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                store.write_repos_dim(conn, [{"key": "o/r", "org": "o", "name": "r", "element": "E"}])
                semantic_editor.save(conn, "global", "",
                    {"categories": {"labels": {}, "types": {}}, "stages": {"statuses": {}},
                     "ci": {"roles": {"CI": "gate", "Nightly": "nightly"}}})
                store.write_ci_runs(conn, [
                    {"repo": "o/r", "run_id": 1, "workflow": "CI", "event": "pull_request",
                     "conclusion": "success", "created_at": "2026-07-01T00:00:00Z", "duration_s": 100},
                    {"repo": "o/r", "run_id": 2, "workflow": "CI", "event": "pull_request",
                     "conclusion": "failure", "created_at": "2026-07-01T00:00:00Z", "duration_s": 200},
                    {"repo": "o/r", "run_id": 3, "workflow": "CI", "event": "schedule",
                     "conclusion": "success", "created_at": "2026-07-01T00:00:00Z"},   # wrong event
                    {"repo": "o/r", "run_id": 4, "workflow": "Nightly", "event": "pull_request",
                     "conclusion": "failure", "created_at": "2026-07-01T00:00:00Z"}])   # not gate
                m = semantic_metrics.ci_metrics(conn, "2026-01-01T00:00:00Z", "2026-12-31T00:00:00Z")
                self.assertEqual(m["ci_gate_runs"], 2)      # runs 1 & 2 only
                self.assertEqual(m["ci_pass_rate"], 50.0)
                self.assertEqual(m["ci_median_duration_s"], 150)


class PrMetricsTest(unittest.TestCase):
    def _pr(self, n, state, merged_at="", additions=None, files=None, reviews=None,
            revert=0, closes=0):
        return {"repo": "o/r", "number": n, "org": "o", "author_login": "a",
                "created_at": "2026-07-01T00:00:00Z", "merged_at": merged_at,
                "review_requested_at": "", "classification": "", "is_migration": 0,
                "is_bot": 0, "state": state, "closed_at": "", "additions": additions,
                "deletions": 0, "changed_files": files, "review_count": reviews,
                "comment_count": 0, "author_association": "MEMBER",
                "closes_issues": closes, "is_revert": revert, "is_draft": 0, "labels": []}

    def test_merge_abandon_size_reverts(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                store.write_prs(conn, [
                    self._pr(1, "MERGED", merged_at="2026-07-02T00:00:00Z", additions=10,
                             files=2, reviews=1, closes=1),
                    self._pr(2, "MERGED", merged_at="2026-07-02T00:00:00Z", additions=30,
                             files=6, reviews=0),
                    self._pr(3, "CLOSED", additions=100, files=20, reviews=2, revert=1),
                    self._pr(4, "OPEN", additions=50, files=10, reviews=1)])
                m = semantic_metrics.pr_metrics(conn, "2026-01-01T00:00:00Z",
                                                "2026-12-31T00:00:00Z")
                self.assertEqual(m["prs_total"], 4)
                self.assertEqual(m["pr_merge_rate"], 50.0)          # 2 of 4
                self.assertEqual(m["pr_abandon_rate"], 25.0)        # 1 CLOSED-unmerged
                self.assertEqual(m["pr_reverts"], 1)
                self.assertEqual(m["pr_median_additions"], 40)      # median(10,30,100,50)
                self.assertEqual(m["pr_linked_rate"], 25.0)         # 1 closes an issue
                self.assertEqual(m["pr_reviewed_rate"], 75.0)       # 3 of 4 have >0 reviews


class RepoSliceTest(unittest.TestCase):
    def test_metrics_scoped_to_a_repo_slice(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                store.write_issues(conn, [
                    {"repo": "o/a", "number": 1, "org": "o", "author_login": "x",
                     "created_at": "2026-07-01T00:00:00Z", "is_bug": 0, "is_feature": 0,
                     "is_migration": 0, "is_bot": 0, "issue_type": "", "labels": []},
                    {"repo": "o/b", "number": 2, "org": "o", "author_login": "x",
                     "created_at": "2026-07-01T00:00:00Z", "is_bug": 0, "is_feature": 0,
                     "is_migration": 0, "is_bot": 0, "issue_type": "", "labels": []}])
                w = "2026-01-01T00:00:00Z", "2026-12-31T00:00:00Z"
                self.assertEqual(semantic_metrics.issue_metrics(conn, *w)["issues_total"], 2)
                self.assertEqual(
                    semantic_metrics.issue_metrics(conn, *w, repos=["o/a"])["issues_total"], 1)
                self.assertEqual(
                    semantic_metrics.issue_metrics(conn, *w, repos=[])["issues_total"], 0)


class WindowBlockTest(unittest.TestCase):
    def test_combines_issue_and_ci_keys(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                b = semantic_metrics.window_block(conn, "2026-01-01T00:00:00Z",
                                                  "2026-12-31T00:00:00Z")
                for k in ("issues_total", "issue_close_rate", "issues_by_category",
                          "ci_pass_rate", "ci_gate_runs"):
                    self.assertIn(k, b)


class CatalogRegistrationTest(unittest.TestCase):
    def test_semantic_metrics_are_in_the_catalog(self):
        import metrics_registry as mreg
        names = mreg.names()
        for m in ("issues_by_category", "issue_close_rate", "defect_rate",
                  "issue_median_time_to_close_days", "ci_pass_rate", "ci_gate_runs",
                  "ci_median_duration_s", "pr_merge_rate", "pr_abandon_rate",
                  "pr_median_additions", "pr_reverts", "pr_linked_rate",
                  "pr_reviewed_rate"):
            self.assertIn(m, names, f"{m} missing from /metrics catalog")


if __name__ == "__main__":
    unittest.main()
