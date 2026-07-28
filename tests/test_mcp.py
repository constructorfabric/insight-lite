import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

try:
    import mcp_server
    HAVE_MCP = True
except Exception:                       # pragma: no cover - mcp SDK not installed
    HAVE_MCP = False


@unittest.skipUnless(HAVE_MCP, "mcp SDK not installed")
class McpToolsTest(unittest.TestCase):
    def _seed(self, tmp):
        import store
        conn = store.connect()
        store.write_issues(conn, [{"repo": "o/r", "number": 1, "org": "o",
            "author_login": "a", "created_at": "2026-07-01T00:00:00Z", "is_bug": 0,
            "is_feature": 0, "is_migration": 0, "is_bot": 0, "issue_type": "Bug",
            "labels": ["bug"]}])
        conn.close()

    def test_sql_query_read_only_and_rejects_writes(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                self._seed(tmp)
                ok = mcp_server.sql_query("SELECT COUNT(*) n FROM issue")
                self.assertEqual(ok["rows"], [{"n": 1}])
                self.assertIn("error", mcp_server.sql_query("DELETE FROM issue"))
                self.assertIn("error", mcp_server.sql_query("SELECT 1; DROP TABLE issue"))
                self.assertIn("error", mcp_server.sql_query("UPDATE issue SET number=9"))

    def test_describe_schema_and_delivery(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                self._seed(tmp)
                self.assertIn("issue", mcp_server.describe_schema()["tables"])
                self.assertEqual(mcp_server.delivery()["issues_total"], 1)

    def test_scope_validation(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                self._seed(tmp)
                self.assertIn("error", mcp_server.contribution(scope="bogusscope"))


if __name__ == "__main__":
    unittest.main()
