"""describe_schema() must say which of repo's two identifiers a `repo` column holds.

The schema declares no foreign keys, so a list of table columns cannot answer it: `repo`
has both `key` ('org/name') and `name` ('name'), and every fact table has a bare `repo`
column. Guessing wrong does not error — it returns an empty result. The assistant wrote

    WHERE repo IN (SELECT name FROM repo WHERE element = 'X')

which succeeds, matches nothing, and produced an answer about an element from no rows at
all; the transcript then shows three more tool hops spent hunting for why.

Measured on production before this was added: for all twelve tables with a `repo` column,
joining repo.key matched every row and joining repo.name matched zero. These tests pin the
DIRECTION on a fixture rather than that measurement, so they hold for any database, and
pin that the description stays complete as tables are added.
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
def _store():
    with TemporaryDirectory() as tmp:
        with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
            import store
            conn = store.connect()
            try:
                yield conn
            finally:
                conn.close()


def _seed(conn):
    """One repo whose key and name DIFFER — the whole point — and one commit on it."""
    conn.execute("INSERT INTO repo (key, org, name, element) VALUES "
                 "('acme/widgets', 'acme', 'widgets', 'Widgets')")
    conn.execute("INSERT INTO commits (repo, sha, committed_at, author_login, "
                 "additions, deletions, is_bot) VALUES "
                 "('acme/widgets', 'deadbeef', '2026-01-01T00:00:00Z', 'ann', 1, 0, 0)")
    conn.commit()


class SchemaJoinsTest(unittest.TestCase):
    def test_it_names_the_column_that_actually_joins(self):
        with _store() as conn:
            _seed(conn)
            import tooldefs
            joins = tooldefs.describe_schema()["joins"]
            self.assertEqual(joins["repo"]["join_to"], "repo.key")
            self.assertEqual(joins["person"]["join_to"], "person.login")

    def test_the_direction_it_names_is_the_one_that_matches_rows(self):
        """The assertion that would have caught the trap: same query, both identifiers."""
        with _store() as conn:
            _seed(conn)
            by_key = conn.execute(
                "SELECT count(*) FROM commits WHERE repo IN "
                "(SELECT key FROM repo WHERE element='Widgets')").fetchone()[0]
            by_name = conn.execute(
                "SELECT count(*) FROM commits WHERE repo IN "
                "(SELECT name FROM repo WHERE element='Widgets')").fetchone()[0]
            self.assertEqual(by_key, 1)
            self.assertEqual(by_name, 0, "if this ever matches, the warning is now wrong "
                                         "and describe_schema must be updated")

    def test_the_warning_says_the_failure_is_silent(self):
        """A warning that only says "use key" invites the reader to assume the other one
        errors. The reason this cost hops is that it does not."""
        with _store() as conn:
            _seed(conn)
            import tooldefs
            warning = tooldefs.describe_schema()["joins"]["repo"]["warning"].lower()
            self.assertIn("repo.name", warning)
            self.assertTrue("zero rows" in warning or "does not error" in warning)

    def test_every_table_carrying_a_repo_column_is_listed(self):
        """Derived from the live schema, not hand-listed, so a new table cannot go
        undocumented — this test pins that the derivation stays honest."""
        with _store() as conn:
            _seed(conn)
            import tooldefs
            d = tooldefs.describe_schema()
            expected = {f"{t}.repo" for t, cols in d["tables"].items()
                        if t != "repo" and "repo" in cols}
            self.assertEqual(set(d["joins"]["repo"]["columns"]), expected)
            self.assertIn("commits.repo", expected, "fixture sanity")

    def test_login_columns_are_listed_under_their_real_names(self):
        with _store() as conn:
            _seed(conn)
            import tooldefs
            d = tooldefs.describe_schema()
            cols = set(d["joins"]["person"]["columns"])
            self.assertIn("commits.author_login", cols)
            self.assertNotIn("person.login", cols, "person is the target, not a source")
            for c in cols:
                table, col = c.split(".")
                self.assertIn(col, d["tables"][table])


if __name__ == "__main__":
    unittest.main()
