import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import collect
import store


class ParseProjectItemsTest(unittest.TestCase):
    def test_issue_pr_and_draft(self):
        nodes = [
            {"id": "I1", "type": "ISSUE",
             "content": {"__typename": "Issue", "number": 42, "title": "a bug",
                         "repository": {"nameWithOwner": "o/r"}},
             "status": {"name": "In Review"}},
            {"id": "P1", "type": "PULL_REQUEST",
             "content": {"__typename": "PullRequest", "number": 7, "title": "a pr",
                         "repository": {"nameWithOwner": "o/r"}},
             "status": {"name": "Done"}},
            {"id": "D1", "type": "DRAFT_ISSUE",
             "content": {"__typename": "DraftIssue", "title": "idea"},
             "status": None},
        ]
        rows = collect._parse_project_items("o", 12, nodes)
        self.assertEqual(rows[0], {"item_id": "I1", "project": "o/12", "item_type": "issue",
                                   "repo": "o/r", "number": 42, "status_raw": "In Review",
                                   "title": "a bug", "updated_at": None})
        self.assertEqual(rows[1]["item_type"], "pull_request")
        self.assertEqual(rows[1]["status_raw"], "Done")
        self.assertEqual(rows[2]["item_type"], "draft")
        self.assertIsNone(rows[2]["repo"])
        self.assertIsNone(rows[2]["status_raw"])

    def test_redacted_or_empty_content_is_kept_with_nulls(self):
        rows = collect._parse_project_items("o", 1, [{"id": "X", "content": None, "status": None}])
        self.assertEqual(rows[0]["item_id"], "X")
        self.assertIsNone(rows[0]["item_type"])
        self.assertIsNone(rows[0]["number"])


class ParseProjectFieldsTest(unittest.TestCase):
    def test_extracts_typed_fields_skips_builtins_and_empty(self):
        nodes = [{
            "id": "I1",
            "content": {"__typename": "Issue", "number": 42,
                        "repository": {"nameWithOwner": "o/r"}},
            "fieldValues": {"nodes": [
                {"__typename": "ProjectV2ItemFieldSingleSelectValue", "name": "High",
                 "field": {"name": "Priority"}},
                {"__typename": "ProjectV2ItemFieldNumberValue", "number": 5.0,
                 "field": {"name": "Estimate"}},
                {"__typename": "ProjectV2ItemFieldIterationValue", "title": "Sprint 7",
                 "field": {"name": "Iteration"}},
                {"__typename": "ProjectV2ItemFieldSingleSelectValue", "name": "Todo",
                 "field": {"name": "Status"}},          # skipped (captured elsewhere)
                {"__typename": "ProjectV2ItemFieldTextValue", "text": "",
                 "field": {"name": "Notes"}},           # skipped (empty)
            ]},
        }]
        rows = collect._parse_project_fields("o", 3, nodes)
        got = {r["field"]: r["value"] for r in rows}
        self.assertEqual(got, {"Priority": "High", "Estimate": "5", "Iteration": "Sprint 7"})
        self.assertEqual(rows[0]["repo"], "o/r")
        self.assertEqual(rows[0]["number"], 42)


class WriteSnapshotTablesTest(unittest.TestCase):
    def test_fields_repo_membership_roundtrip(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                store.write_work_item_fields(conn, "2026-07-15T12:00:00Z", [
                    {"item_id": "I1", "project": "o/1", "repo": "o/r", "number": 1,
                     "field": "Priority", "value": "High"}])
                self.assertEqual(
                    conn.execute("SELECT value FROM work_item_field WHERE field='Priority'").fetchone()[0],
                    "High")
                store.write_repo_snapshot(conn, "2026-07-15", [
                    {"repo": "o/r", "stars": 12, "forks": 3, "archived": False,
                     "element": "Core", "classification": "app"}])
                self.assertEqual(
                    conn.execute("SELECT stars FROM repo_snapshot WHERE repo='o/r'").fetchone()[0], 12)
                store.write_membership_snapshot(conn, "2026-07-15", [
                    {"org": "o", "login": "alice", "role": "admin"},
                    {"org": "o", "login": "bob", "role": "member"}])
                self.assertEqual(
                    conn.execute("SELECT role FROM membership_snapshot WHERE login='alice'").fetchone()[0],
                    "admin")


class WriteWorkItemStatusTest(unittest.TestCase):
    def test_additive_across_days_same_day_replaces(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                store.write_work_item_status(conn, "2026-07-10", [
                    {"item_id": "I1", "project": "o/1", "item_type": "issue",
                     "repo": "o/r", "number": 1, "status_raw": "Todo", "title": "x"}])
                store.write_work_item_status(conn, "2026-07-11", [
                    {"item_id": "I1", "project": "o/1", "item_type": "issue",
                     "repo": "o/r", "number": 1, "status_raw": "In Progress", "title": "x"}])
                all_rows = store.read_work_item_status(conn)
                self.assertEqual(len(all_rows), 2)   # both days kept — forward history
                self.assertEqual({r["date"]: r["status_raw"] for r in all_rows},
                                 {"2026-07-10": "Todo", "2026-07-11": "In Progress"})
                # a same-day re-run replaces ONLY that day
                store.write_work_item_status(conn, "2026-07-11", [
                    {"item_id": "I1", "project": "o/1", "item_type": "issue",
                     "repo": "o/r", "number": 1, "status_raw": "Done", "title": "x"}])
                by_day = {r["date"]: r["status_raw"] for r in store.read_work_item_status(conn)}
                self.assertEqual(by_day, {"2026-07-10": "Todo", "2026-07-11": "Done"})


if __name__ == "__main__":
    unittest.main()
