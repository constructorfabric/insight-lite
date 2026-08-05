"""The four tools built from what the assistant kept writing by hand.

The chat transcript is the specification here. Of 81 sql_query calls, 26 touched
person_runs and five were near-identical hand-rolled "rank people by commits in this
element" queries whose only difference was how each tried to turn an element into a set of
repos — the thing _repos() already did correctly, and the thing the model got wrong in a way
that returns zero rows without erroring.

So the tests that matter are not "does it return rows". They are: does scoping resolve the
way the report resolves it, does ranking rank, and do the failure paths say something the
model can act on instead of returning zeros that read as inactivity.
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
            _seed(conn)
            conn.close()
            import tooldefs
            yield tooldefs


def _seed(conn):
    """Two elements, so scoping has something to get wrong. Note the repo KEY is
    'org/name' while the name is bare — the distinction the model kept tripping over."""
    conn.execute("INSERT INTO repo (key, org, name, element) VALUES "
                 "('acme/alpha', 'acme', 'alpha', 'Alpha')")
    conn.execute("INSERT INTO repo (key, org, name, element) VALUES "
                 "('acme/beta', 'acme', 'beta', 'Beta')")
    for login, name in (("ann", "Ann Alpha"), ("bob", "Bob Beta")):
        conn.execute("INSERT INTO person (login, name, company, is_member, emails, "
                     "surviving_code_human, reviews_given) VALUES (?,?,?,1,?,?,?)",
                     (login, name, "Acme", f"{login}@acme.test",
                      100 if login == "ann" else 50, 7))
    # ann: three commits in Alpha. bob: one in Alpha, five in Beta.
    rows = ([("acme/alpha", "ann", i) for i in range(3)]
            + [("acme/alpha", "bob", 100)]
            + [("acme/beta", "bob", 200 + i) for i in range(5)])
    for repo, login, n in rows:
        conn.execute("INSERT INTO commits (repo, sha, committed_at, author_login, "
                     "additions, deletions, meaningful_additions, is_bot) VALUES "
                     "(?,?,?,?,?,0,?,0)",
                     (repo, f"sha{repo[-1]}{login}{n}", "2026-03-01T00:00:00Z", login,
                      10, 10))
    conn.execute("INSERT INTO runs (date, generated_at, payload) VALUES "
                 "('2026-03-02', '2026-03-02T04:00:00Z', '{}')")
    conn.commit()


class TopContributorsTest(unittest.TestCase):
    def test_it_ranks_by_the_metric_asked_for(self):
        with _tools() as t:
            rows = t.top_contributors(metric="commits")["rows"]
            self.assertEqual([r["login"] for r in rows], ["bob", "ann"])
            self.assertEqual([r["commits"] for r in rows], [6, 3])

    def test_scoping_to_an_element_resolves_the_way_the_report_does(self):
        """The regression this tool exists for. The model's hand-written version filtered
        `repo IN (SELECT name FROM repo WHERE element=?)`, which matches nothing because a
        commit's repo column holds the KEY — and returns an empty result rather than an
        error, so the answer came out of no data at all."""
        with _tools() as t:
            rows = t.top_contributors(scope="element:Alpha", metric="commits")["rows"]
            self.assertEqual([(r["login"], r["commits"]) for r in rows],
                             [("ann", 3), ("bob", 1)])
            beta = t.top_contributors(scope="element:Beta", metric="commits")["rows"]
            self.assertEqual([(r["login"], r["commits"]) for r in beta], [("bob", 5)])

    def test_a_bad_scope_says_what_a_scope_looks_like(self):
        with _tools() as t:
            err = t.top_contributors(scope="person:ann")["error"]
            self.assertIn("org|element|repo|project", err)

    def test_a_bad_metric_lists_the_metrics(self):
        with _tools() as t:
            err = t.top_contributors(metric="vibes")["error"]
            for m in ("commits", "prs", "specs"):
                self.assertIn(m, err)

    def test_the_limit_is_bounded_and_the_total_is_reported_separately(self):
        with _tools() as t:
            out = t.top_contributors(limit=1)
            self.assertEqual(len(out["rows"]), 1)
            self.assertEqual(out["people_total"], 2, "the cut must not hide the count")


class FindPersonTest(unittest.TestCase):
    def test_it_resolves_a_human_name_to_a_login(self):
        with _tools() as t:
            rows = t.find_person("Barkhatov")["rows"]
            self.assertEqual(rows, [])                     # not in this fixture
            rows = t.find_person("Ann")["rows"]
            self.assertEqual([r["login"] for r in rows], ["ann"])

    def test_it_matches_partial_logins_and_emails(self):
        with _tools() as t:
            self.assertEqual([r["login"] for r in t.find_person("bo")["rows"]], ["bob"])
            self.assertEqual([r["login"] for r in t.find_person("@acme.test")["rows"]],
                             ["ann", "bob"])

    def test_an_empty_query_is_refused_rather_than_matching_everyone(self):
        with _tools() as t:
            self.assertIn("required", t.find_person("  ")["error"])

    def test_no_match_is_an_empty_list_not_an_error(self):
        with _tools() as t:
            out = t.find_person("nobody")
            self.assertEqual(out["match_count"], 0)
            self.assertNotIn("error", out)


class PersonActivityTest(unittest.TestCase):
    def test_it_totals_one_persons_window(self):
        with _tools() as t:
            out = t.person_activity("ann", since="2026-02-01", until="2026-03-31")
            self.assertEqual(out["commits"], 3)
            self.assertEqual(out["name"], "Ann Alpha")

    def test_a_window_that_excludes_the_work_reports_zero_not_an_error(self):
        with _tools() as t:
            out = t.person_activity("ann", since="2026-06-01", until="2026-06-30")
            self.assertEqual(out["commits"], 0)
            self.assertNotIn("note", out, "the login is real; the window is just empty")

    def test_an_unknown_login_is_flagged_rather_than_read_as_inactive(self):
        """Zeros for a misspelled login look exactly like zeros for an idle person."""
        with _tools() as t:
            out = t.person_activity("annn")
            self.assertEqual(out["commits"], 0)
            self.assertIn("find_person", out["note"])


class DataFreshnessTest(unittest.TestCase):
    def test_it_reports_the_run_and_the_newest_commit(self):
        with _tools() as t:
            out = t.data_freshness()
            self.assertEqual(out["latest_run_date"], "2026-03-02")
            self.assertEqual(out["generated_at"], "2026-03-02T04:00:00Z")
            self.assertEqual(out["newest_commit_at"], "2026-03-01T00:00:00Z")


class RegistrationTest(unittest.TestCase):
    def test_all_four_are_callable_tools_with_declarations(self):
        """DISPATCH is what the chat and the MCP server both dispatch through, and
        declarations() is what the model is told about. Being in one and not the other is
        the failure mode."""
        with _tools() as t:
            names = {d["name"] for d in t.declarations()}
            for fn in ("top_contributors", "person_activity", "find_person",
                       "data_freshness"):
                self.assertIn(fn, t.DISPATCH, fn)
                self.assertIn(fn, names, fn)

    def test_every_declaration_documents_itself(self):
        with _tools() as t:
            for d in t.declarations():
                self.assertTrue((d.get("description") or "").strip(), d["name"])


if __name__ == "__main__":
    unittest.main()
