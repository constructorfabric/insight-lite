import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import collect
import store


class ParseIssueEnrichmentTest(unittest.TestCase):
    def test_full_and_sparse_nodes(self):
        nodes = [
            {"number": 10, "issueType": {"name": "Bug"}, "state": "CLOSED",
             "stateReason": "COMPLETED", "closedAt": "2026-07-01T00:00:00Z",
             "milestone": {"title": "26.07"},
             "labels": {"nodes": [{"name": "bug"}, {"name": "ci"}]},
             "assignees": {"nodes": [{"login": "alice"}]}},
            {"number": 11, "issueType": None, "state": "OPEN", "stateReason": None,
             "closedAt": None, "milestone": None,
             "labels": {"nodes": []}, "assignees": {"nodes": []}},
        ]
        e = collect._parse_issue_enrichment("your-old-org", "example-core", nodes)
        r = e[("your-old-org/example-core", 10)]
        self.assertEqual(r["issue_type"], "Bug")
        self.assertEqual(r["labels"], ["bug", "ci"])
        self.assertEqual(r["state_reason"], "COMPLETED")
        self.assertEqual(r["assignees"], ["alice"])
        self.assertEqual(r["milestone"], "26.07")
        # sparse issue: everything empty, no native type
        s = e[("your-old-org/example-core", 11)]
        self.assertEqual(s["issue_type"], "")
        self.assertEqual(s["labels"], [])
        self.assertEqual(s["closed_at"], "")


class IssueEnrichmentStorageTest(unittest.TestCase):
    def test_new_columns_roundtrip_with_json(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                row = {"repo": "o/r", "number": 5, "org": "o", "author_login": "a",
                       "created_at": "2026-07-01T00:00:00Z", "is_bug": 1,
                       "is_feature": 0, "is_migration": 0, "is_bot": 0,
                       "issue_type": "Bug", "labels": ["bug", "ci"], "state": "CLOSED",
                       "state_reason": "COMPLETED", "closed_at": "2026-07-02T00:00:00Z",
                       "assignees": ["alice", "bob"], "milestone": "26.07"}
                store.write_issues(conn, [row])
                got = conn.execute(
                    "SELECT issue_type, labels, state, state_reason, assignees, milestone "
                    "FROM issue WHERE repo='o/r' AND number=5").fetchone()
                self.assertEqual(got["issue_type"], "Bug")
                self.assertEqual(json.loads(got["labels"]), ["bug", "ci"])
                self.assertEqual(json.loads(got["assignees"]), ["alice", "bob"])
                self.assertEqual(got["state_reason"], "COMPLETED")
                self.assertEqual(got["milestone"], "26.07")

    def test_base_row_without_enrichment_still_writes(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                store.write_issues(conn, [{"repo": "o/r", "number": 1, "org": "o",
                                           "author_login": "a", "created_at": "x",
                                           "is_bug": 0, "is_feature": 0,
                                           "is_migration": 0, "is_bot": 0}])
                got = conn.execute("SELECT issue_type, labels FROM issue").fetchone()
                self.assertIn(got["issue_type"], (None, ""))   # unenriched → empty/null


if __name__ == "__main__":
    unittest.main()
