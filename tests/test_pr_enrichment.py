import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import collect
import store


class ParsePrEnrichmentTest(unittest.TestCase):
    def test_size_state_and_counts(self):
        nodes = [{
            "number": 20, "title": "add feature", "state": "MERGED",
            "closedAt": "2026-07-01T00:00:00Z", "additions": 120, "deletions": 8,
            "changedFiles": 5, "authorAssociation": "MEMBER", "isDraft": False,
            "updatedAt": "2026-07-01T00:00:00Z",
            "reviews": {"totalCount": 2}, "comments": {"totalCount": 3},
            "closingIssuesReferences": {"totalCount": 1},
            "labels": {"nodes": [{"name": "feature"}]},
        }]
        r = collect._parse_pr_enrichment("o", "r", nodes)[("o/r", 20)]
        self.assertEqual(r["state"], "MERGED")
        self.assertEqual((r["additions"], r["deletions"], r["changed_files"]), (120, 8, 5))
        self.assertEqual(r["review_count"], 2)
        self.assertEqual(r["closes_issues"], 1)
        self.assertEqual(r["is_revert"], 0)
        self.assertEqual(r["labels"], ["feature"])

    def test_revert_and_closed_unmerged(self):
        nodes = [{"number": 21, "title": 'Revert "add feature"', "state": "CLOSED",
                  "closedAt": "2026-07-02T00:00:00Z", "isDraft": True,
                  "updatedAt": "2026-07-02T00:00:00Z"}]
        r = collect._parse_pr_enrichment("o", "r", nodes)[("o/r", 21)]
        self.assertEqual(r["is_revert"], 1)
        self.assertEqual(r["is_draft"], 1)
        self.assertEqual(r["state"], "CLOSED")     # closed-unmerged = abandoned
        self.assertIsNone(r["additions"])          # missing field -> None


class PrEnrichmentStorageTest(unittest.TestCase):
    def test_new_columns_roundtrip(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                row = {"repo": "o/r", "number": 9, "org": "o", "author_login": "a",
                       "created_at": "x", "merged_at": "", "review_requested_at": "",
                       "classification": "", "is_migration": 0, "is_bot": 0,
                       "state": "MERGED", "closed_at": "y", "additions": 50,
                       "deletions": 5, "changed_files": 3, "review_count": 1,
                       "comment_count": 2, "author_association": "MEMBER",
                       "closes_issues": 1, "is_revert": 0, "is_draft": 0,
                       "labels": ["feature", "spec"]}
                store.write_prs(conn, [row])
                got = conn.execute("SELECT state, additions, review_count, "
                                   "author_association, labels FROM pull_request "
                                   "WHERE repo='o/r' AND number=9").fetchone()
                self.assertEqual(got["state"], "MERGED")
                self.assertEqual(got["additions"], 50)
                self.assertEqual(got["author_association"], "MEMBER")
                self.assertEqual(json.loads(got["labels"]), ["feature", "spec"])


if __name__ == "__main__":
    unittest.main()
