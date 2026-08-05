"""A failed sql_query must tell the model what the right names are.

Fourteen of the eighty-one sql_query calls in the production transcript failed, and every
one was a name or dialect guess: `author` for author_login (three times), `pr` for
pull_request (twice), `commit` — SQL syntax, not a table — for commits (twice), plus
created_at, id, is_bot, surviving_code_gh, and ILIKE / information_schema straight from
Postgres. Each came back as the bare sqlite message, which costs a tool round-trip out of
eight and teaches nothing: the transcript shows the model responding by calling
describe_schema again, or SELECT sql FROM sqlite_master, or just guessing differently.

The cases below are those exact failures. What is asserted is that the reply carries
somewhere to go, not the wording.
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
            store.connect().close()                  # create the schema
            import tooldefs
            yield tooldefs


class SqlHintTest(unittest.TestCase):
    def test_a_wrong_column_lists_the_columns_of_the_table_asked_about(self):
        with _tools() as t:
            r = t.sql_query("SELECT author FROM commits")
            self.assertIn("no such column", r["error"])
            self.assertIn("commits", r["columns"])
            self.assertIn("author_login", r["columns"]["commits"])
            self.assertIn("commits.author_login", r["did_you_mean"])

    def test_it_lists_only_the_tables_the_query_named(self):
        """Returning the whole schema in an error payload is how discovery gets paid for
        twice — the point is to answer THIS query, not to re-describe the database."""
        with _tools() as t:
            r = t.sql_query("SELECT author FROM commits")
            self.assertEqual(list(r["columns"]), ["commits"])

    def test_a_wrong_table_lists_the_tables_and_guesses_the_abbreviation(self):
        with _tools() as t:
            r = t.sql_query("SELECT author FROM pr")
            self.assertIn("no such table", r["error"])
            self.assertIn("pull_request", r["tables"])
            self.assertIn("pull_request", r["did_you_mean"],
                          "'pr' is the commonest guess in the transcript and difflib alone "
                          "scores it too low to suggest — the acronym rule is what catches it")

    def test_a_reserved_word_is_named_as_syntax_rather_than_a_missing_table(self):
        """`FROM commit` reports 'near "commit": syntax error', which reads like a typo in
        the query rather than a wrong table name."""
        with _tools() as t:
            r = t.sql_query("SELECT author FROM commit WHERE 1")
            self.assertIn("syntax error", r["error"])
            self.assertIn("commits", r["reserved_word"])

    def test_postgres_dialect_is_called_out(self):
        with _tools() as t:
            for sql in ("SELECT login FROM person WHERE name ILIKE '%a%'",
                        "SELECT column_name FROM information_schema.columns"):
                r = t.sql_query(sql)
                self.assertIn("SQLite", r["dialect"], sql)

    def test_a_successful_query_carries_no_hints(self):
        with _tools() as t:
            r = t.sql_query("SELECT count(*) AS n FROM commits")
            self.assertEqual(r["row_count"], 1)
            for noise in ("error", "did_you_mean", "tables", "columns", "dialect",
                          "reserved_word"):
                if noise == "columns":
                    self.assertIsInstance(r["columns"], list)   # the result's own columns
                else:
                    self.assertNotIn(noise, r)

    def test_the_hint_never_replaces_or_masks_the_error(self):
        with _tools() as t:
            r = t.sql_query("SELECT nope FROM commits")
            self.assertIn("error", r)
            self.assertIn("nope", r["error"])

    def test_a_hint_that_blows_up_still_returns_the_error(self):
        with _tools() as t:
            with patch.object(t, "_sql_hint", side_effect=RuntimeError("boom")):
                r = t.sql_query("SELECT author FROM commits")
            self.assertIn("no such column", r["error"])
            self.assertNotIn("did_you_mean", r)

    def test_rejections_that_never_reach_sqlite_are_untouched(self):
        with _tools() as t:
            self.assertIn("one statement", t.sql_query("SELECT 1; SELECT 2")["error"])
            self.assertIn("only SELECT", t.sql_query("DELETE FROM commits")["error"])


if __name__ == "__main__":
    unittest.main()
