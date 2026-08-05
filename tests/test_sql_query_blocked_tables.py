"""The assistant's SQL tool must not be able to read credentials.

`secret` is a key/value table; on production it holds the MCP API token. `sql_query`
accepts any statement that begins with SELECT or WITH and applied no table restriction, so
the chat — authenticated, but any viewer — could be asked for the token and would run the
query and could put the value in its answer. No analytical question needs that table.

Found while inlining the table list into the assistant's system instruction: that change
would have advertised `secret` in the always-present context, which is what prompted
looking at what the tool could actually reach.

Enforcement is SQLite's authorizer, not a pattern over the SQL text, because the authorizer
sees the RESOLVED table name — the tests below are the evasions a regex over `FROM secret`
would miss.
"""
import contextlib
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))


@contextlib.contextmanager
def _tools():
    with TemporaryDirectory() as tmp:
        with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
            import store
            conn = store.connect()
            conn.execute("INSERT INTO secret (key, value, updated_at) VALUES "
                         "('mcp_token', 'tok_do_not_leak', '2026-01-01T00:00:00Z')")
            conn.execute("INSERT INTO commits (repo, sha, committed_at, author_login, "
                         "additions, deletions, is_bot) VALUES "
                         "('o/r', 'abc', '2026-01-01T00:00:00Z', 'ann', 1, 0, 0)")
            conn.commit()
            conn.close()
            import tooldefs
            yield tooldefs


class BlockedTablesTest(unittest.TestCase):
    EVASIONS = (
        "SELECT key, value FROM secret",
        'SELECT * FROM "secret"',
        "SELECT s.value FROM secret AS s",
        "SELECT (SELECT value FROM secret LIMIT 1) AS v",
        "WITH x AS (SELECT value FROM secret) SELECT * FROM x",
        "SELECT value FROM SECRET",
        "SELECT c.sha FROM commits c JOIN secret s ON 1=1",
    )

    def test_every_route_to_the_secret_table_is_refused(self):
        """Two wordings, not one: a column read denied inside a statement comes back as
        "access to secret.value is prohibited", while a denied JOIN surfaces as sqlite's
        blunter "not authorized". Both are refusals, and pinning either phrase alone would
        make this test a spelling check on a library."""
        with _tools() as t:
            for sql in self.EVASIONS:
                r = t.sql_query(sql)
                self.assertIn("error", r, sql)
                self.assertTrue("prohibited" in r["error"] or "not authorized" in r["error"],
                                f"{sql} -> {r['error']}")
                self.assertNotIn("rows", r, sql)

    def test_the_value_never_appears_in_a_reply(self):
        """The assertion that matters: whatever the error says, the token is not in it."""
        with _tools() as t:
            for sql in self.EVASIONS:
                self.assertNotIn("tok_do_not_leak", repr(t.sql_query(sql)), sql)

    def test_ordinary_queries_are_unaffected(self):
        with _tools() as t:
            r = t.sql_query("SELECT count(*) AS n FROM commits")
            self.assertEqual(r["rows"], [{"n": 1}])

    def test_the_table_is_not_offered_in_the_schema(self):
        with _tools() as t:
            self.assertNotIn("secret", t.describe_schema()["tables"])

    def test_the_table_is_not_offered_in_a_failed_query_hint(self):
        """The hint lists table names; it must not be the thing that reveals this one."""
        with _tools() as t:
            r = t.sql_query("SELECT * FROM nosuchthing")
            self.assertIn("tables", r)
            self.assertNotIn("secret", r["tables"])

    def test_the_block_list_is_not_empty(self):
        with _tools() as t:
            self.assertIn("secret", t.BLOCKED_TABLES)

    def test_the_assistants_grounding_does_not_mention_it(self):
        with _tools() as t:  # noqa: F841 — REPORT_DB must be patched for _grounding
            import chat_agent
            chat_agent._SYSTEM_FULL = None          # resolved once per process
            try:
                self.assertNotIn("secret", chat_agent._grounding())
            finally:
                chat_agent._SYSTEM_FULL = None


if __name__ == "__main__":
    unittest.main()
