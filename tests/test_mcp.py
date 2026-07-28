import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

try:
    import mcp                          # the SDK itself
except ImportError:                     # pragma: no cover - mcp SDK not installed
    HAVE_MCP = False
else:
    HAVE_MCP = True
    # Deliberately NOT guarded. This was `try: import mcp_server / except Exception:
    # HAVE_MCP = False`, which turned a broken server into a skipped module: when mcp
    # 2.0 moved mcp.server.fastmcp, every test here quietly stopped running, the suite
    # stayed green, and the build published an MCP service that crash-looped on import.
    # If the SDK is installed, failing to import mcp_server is a failure, not a skip.
    import mcp_server


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
