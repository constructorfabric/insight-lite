"""A question about a person must not be refused because the PAGE had a scope.

Observed in production after the tools shipped. Somebody asked "what is my 30d friction?"
while looking at an element page, so the panel passed that element as the scope. They are
under the activity floor INSIDE that element and scored fine outside it, so
developer_score returned "not scored" with a note about the floor and the spelling of the
login. The assistant then spent eight further tool calls checking the login it had been
pointed at, and told the person their data was unavailable — 146,362 tokens for a
non-answer, while the same call with no scope returns a score.

Two changes are pinned here. A scoped miss looks again without the scope and hands back
what it finds, and friction_breakdown answers "how is MY number made" from the parts the
Flow page already computes, so that question stops being reverse-engineered out of
timeline_event.
"""
import contextlib
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

SINCE, UNTIL = "2026-06-01", "2026-07-01"


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
    """Two repos in two elements. Everyone works in Alpha; `visitor` has a single commit
    in Beta and everything else in Alpha — so scoping to Beta puts them under the floor
    while they are comfortably scored overall. That is the production shape."""
    for key, name, el in (("o/alpha", "alpha", "Alpha"), ("o/beta", "beta", "Beta")):
        conn.execute("INSERT INTO repo (key, org, name, element) VALUES (?,'o',?,?)",
                     (key, name, el))
    people = ("ann", "bob", "cat", "visitor")
    for idx, lg in enumerate(people):
        conn.execute("INSERT INTO person (login, name) VALUES (?,?)", (lg, lg.title()))
        for i in range(6 + idx):
            day = f"2026-06-{10 + i:02d}"
            conn.execute(
                "INSERT INTO commits (repo, sha, committed_at, author_login, additions, "
                "meaningful_additions, is_spec, ai_marked, is_bot) "
                "VALUES ('o/alpha', ?, ?, ?, 10, 8, 0, 0, 0)",
                (f"{lg}{i}", f"{day}T00:00:00Z", lg))
            conn.execute(
                "INSERT INTO pull_request (repo, number, org, author_login, created_at, "
                "merged_at, changed_files, review_count, is_revert, is_bot, is_migration) "
                "VALUES ('o/alpha', ?, 'o', ?, ?, ?, 3, 1, 0, 0, 0)",
                (100 * (idx + 1) + i, lg, f"{day}T00:00:00Z", f"{day}T06:00:00Z"))
    # the one crumb in the other element
    conn.execute("INSERT INTO commits (repo, sha, committed_at, author_login, additions, "
                 "meaningful_additions, is_spec, ai_marked, is_bot) "
                 "VALUES ('o/beta', 'beta1', '2026-06-11T00:00:00Z', 'visitor', 1, 1, 0, 0, 0)")
    # Timeline events, because friction is computed from them and from nothing else: an item
    # with no events is not tracked, and a person with fewer than three tracked items has no
    # friction at all. ann gets one reopen across her six PRs — friction 2/6 — while bob gets
    # none, so there is a spread to take a median of.
    for n in (100, 101, 102, 103, 104, 105):
        conn.execute("INSERT INTO timeline_event (repo, number, event, actor_login, "
                     "created_at) VALUES ('o/alpha', ?, 'assigned', 'ann', ?)",
                     (n, "2026-06-12T00:00:00Z"))
    conn.execute("INSERT INTO timeline_event (repo, number, event, actor_login, created_at) "
                 "VALUES ('o/alpha', 100, 'reopened', 'ann', '2026-06-13T00:00:00Z')")
    for n in (200, 201, 202, 203, 204, 205, 206):
        conn.execute("INSERT INTO timeline_event (repo, number, event, actor_login, "
                     "created_at) VALUES ('o/alpha', ?, 'assigned', 'bob', ?)",
                     (n, "2026-06-12T00:00:00Z"))
    conn.commit()


class ScopedMissTest(unittest.TestCase):
    def test_a_scoped_miss_reports_the_score_it_found_without_the_scope(self):
        with _tools() as t:
            out = t.developer_score("visitor", since=SINCE, until=UNTIL, scope="element:Beta")
            self.assertFalse(out["scored"], "under the floor inside Beta")
            self.assertIn("scored_without_scope", out,
                          "the answer exists; refusing to mention it is what cost 8 hops")
            self.assertIsInstance(out["scored_without_scope"]["score"], int)

    def test_the_note_blames_the_scope_and_not_the_spelling(self):
        """The old note sent the assistant to check the login, which was never wrong."""
        with _tools() as t:
            note = t.developer_score("visitor", since=SINCE, until=UNTIL,
                                     scope="element:Beta")["note"]
            self.assertIn("element:Beta", note)
            self.assertIn("scope=''", note)
            self.assertNotIn("find_person", note)

    def test_an_unscoped_miss_still_points_at_the_login(self):
        """With no scope to blame, a miss really can be a wrong login."""
        with _tools() as t:
            out = t.developer_score("nosuchperson", since=SINCE, until=UNTIL)
            self.assertFalse(out["scored"])
            self.assertNotIn("scored_without_scope", out)
            self.assertIn("find_person", out["note"])

    def test_a_person_genuinely_absent_everywhere_is_not_promised_a_score(self):
        with _tools() as t:
            out = t.developer_score("nosuchperson", since=SINCE, until=UNTIL,
                                    scope="element:Beta")
            self.assertNotIn("scored_without_scope", out)

    def test_the_wider_lookup_costs_nothing_on_the_happy_path(self):
        """It must only run on a miss: it is a second full scoring pass."""
        with _tools() as t:
            import store
            calls = []
            real = store.developer_scores

            def counted(*a, **kw):
                calls.append(1)
                return real(*a, **kw)

            with patch.object(store, "developer_scores", counted):
                t.developer_score("ann", since=SINCE, until=UNTIL, scope="element:Alpha")
            self.assertEqual(len(calls), 1, "a hit must not trigger the wider lookup")


class FrictionBreakdownTest(unittest.TestCase):
    def test_it_refuses_an_empty_login(self):
        with _tools() as t:
            self.assertIn("required", t.friction_breakdown("")["error"])

    def test_it_answers_with_the_number_and_the_parts_behind_it(self):
        with _tools() as t:
            out = t.friction_breakdown("ann", since=SINCE, until=UNTIL)
            self.assertTrue(out["found"])
            self.assertEqual(out["owned_items"], 6)
            self.assertAlmostEqual(out["friction_per_item"], 2 / 6, places=2,
                                   msg="one reopen across six owned items")
            self.assertEqual(out["parts"]["reopens_pct_of_items"], 17)
            self.assertIsNotNone(out["team_median"])

    def test_a_login_with_no_flow_data_says_what_to_try(self):
        """Friction needs at least three owned TRACKED items, so somebody with none lands
        here — and the reply has to move the conversation on rather than stopping it."""
        with _tools() as t:
            out = t.friction_breakdown("visitor", since=SINCE, until=UNTIL)
            self.assertFalse(out["found"])
            for hint in ("wider window", "scope=''", "find_person"):
                self.assertIn(hint, out["note"])

    def test_a_listed_person_with_no_friction_value_is_not_reported_as_found(self):
        """`cat` is IN the flow report with friction None — commits and PRs but no timeline
        events, so nothing of hers is tracked. found=True with a null would have the
        assistant answering "your friction is None".

        Written first as `if out["found"]: ... else: ...`, which cannot fail: both branches
        assert something that holds by construction. Third time in two days I have written
        that shape, so it is worth naming — a conditional in a test is usually a missing
        fixture wearing a disguise."""
        with _tools() as t:
            out = t.friction_breakdown("cat", since=SINCE, until=UNTIL)
            self.assertFalse(out["found"], "listed with a null friction is not an answer")
            self.assertIn("note", out)

    def test_the_reply_admits_the_term_it_cannot_break_out(self):
        """The formula has four terms and the flow report exposes three; assignment churn is
        folded into friction. A caller asked to explain a number must not be left to account
        for a term it was never given."""
        with _tools() as t:
            out = t.friction_breakdown("ann", since=SINCE, until=UNTIL)
            self.assertIn("extra assignments", out["formula"])
            self.assertIn("extra assignments", out["not_broken_out"])
            self.assertNotIn("extra_assignments", out["parts"])

    def test_a_bad_scope_is_refused_with_the_shape_of_a_scope(self):
        with _tools() as t:
            self.assertIn("org|element|repo|project",
                          t.friction_breakdown("ann", scope="person:ann")["error"])

    def test_it_carries_the_formula_and_says_the_parts_are_rounded(self):
        """The parts come from the Flow page's rounded percentages: they explain the number,
        they do not reconstruct it digit for digit, and claiming otherwise would invite the
        assistant to present a reconstruction that does not quite add up."""
        with _tools() as t:
            out = t.friction_breakdown("ann", since=SINCE, until=UNTIL)
            self.assertTrue(out["found"], "the fixture seeds timeline events on purpose")
            self.assertIn("owned_items", out)
            self.assertIn("formula", out)
            self.assertIn("PERCENTAGES", out["note"])
            self.assertTrue(out["lower_is_better"])


class CoverageTest(unittest.TestCase):
    """A slice set on one page stays set. Somebody can ask an hour later and read a narrowed
    answer as the whole org, so every scoped tool says what it counted in words the answer
    can quote — a `scope` field is something the assistant may or may not notice."""

    def test_no_scope_says_so_rather_than_saying_nothing(self):
        with _tools() as t:
            for out in (t.top_contributors(since=SINCE, until=UNTIL),
                        t.developer_score("ann", since=SINCE, until=UNTIL),
                        t.friction_breakdown("ann", since=SINCE, until=UNTIL)):
                self.assertIn("whole organisation", out["covers"])

    def test_a_scope_is_named_with_how_much_it_leaves_out(self):
        with _tools() as t:
            out = t.top_contributors(since=SINCE, until=UNTIL, scope="element:Beta")
            self.assertIn("element:Beta", out["covers"])
            self.assertRegex(out["covers"], r"\b1 of 2 repositories\b")
            self.assertIn("not included", out["covers"])

    def test_the_widened_score_says_it_is_org_wide(self):
        """Two populations in one payload: the requested slice, which the top-level `covers`
        describes as excluding everything else, and an org-wide score. Sharing one coverage
        line is how slice-only wording gets attached to an org-wide number."""
        with _tools() as t:
            out = t.developer_score("visitor", since=SINCE, until=UNTIL,
                                    scope="element:Beta")
            self.assertIn("element:Beta", out["covers"])
            self.assertIn("whole organisation", out["scored_without_scope"]["covers"])

    def test_the_line_survives_a_miss_too(self):
        """The miss path is where it matters most: that is the answer most at risk of being
        read as "there is no data" rather than "not in this slice"."""
        with _tools() as t:
            miss = t.developer_score("visitor", since=SINCE, until=UNTIL,
                                     scope="element:Beta")
            self.assertIn("element:Beta", miss["covers"])
            fmiss = t.friction_breakdown("nosuchperson", since=SINCE, until=UNTIL,
                                         scope="element:Beta")
            self.assertIn("element:Beta", fmiss["covers"])

    def test_a_login_absent_from_the_flow_report_does_not_crash(self):
        """It did. Rewriting the guard for the null-friction case dropped the `if not mine`
        that handled a login the report never listed, and the final return then called
        .get on None. The tests at the time did not cover an absent login."""
        with _tools() as t:
            out = t.friction_breakdown("nosuchperson", since=SINCE, until=UNTIL)
            self.assertFalse(out["found"])
            self.assertIn("note", out)


class ChatContextTest(unittest.TestCase):
    """What the chat is told about the page it was asked from."""

    def _bits(self, **kw):
        import server
        return server.chat_context_bits(kw.get("scope", ""), kw.get("period", ""),
                                        kw.get("view", ""))

    def test_a_scope_on_a_person_page_is_flagged_as_not_applying(self):
        """The production failure in one line: the Person page shows org-wide figures while
        its filter bar displays a slice, so a scope carried into a person question
        contradicts the screen."""
        bits = self._bits(scope="element:Insight", period="30 days", view="person/score")
        self.assertTrue(any("Person page ignores scope" in b for b in bits))
        self.assertTrue(any("do not apply this scope" in b for b in bits))

    def test_other_pages_are_not_told_that(self):
        for view in ("delivery", "overview", "flow", "people"):
            bits = self._bits(scope="element:Insight", view=view)
            self.assertFalse(any("ignores scope" in b for b in bits), view)

    def test_no_scope_means_no_warning_even_on_the_person_page(self):
        bits = self._bits(scope="", view="person/activity")
        self.assertTrue(any("whole org" in b for b in bits))
        self.assertFalse(any("ignores scope" in b for b in bits))

    def test_the_absence_of_a_scope_is_stated_rather_than_omitted(self):
        """Saying nothing would let the assistant assume a slice it was never given."""
        self.assertIn("scope=whole org (no slice)", self._bits())


class PromptRuleTest(unittest.TestCase):
    def test_the_assistant_is_told_a_page_scope_is_only_a_default(self):
        with _tools():
            import chat_agent
            chat_agent._SYSTEM_FULL = None
            try:
                sysmsg = chat_agent._system()
            finally:
                chat_agent._SYSTEM_FULL = None
            self.assertIn("DEFAULT, not part of their question", sysmsg)
            self.assertIn("scope=''", sysmsg)
            self.assertIn("SAY WHAT THE ANSWER COVERS", sysmsg,
                          "a person who set a scope and forgot must be told what they are "
                          "reading")


class RegistrationTest(unittest.TestCase):
    def test_friction_breakdown_is_dispatchable_and_declared(self):
        with _tools() as t:
            self.assertIn("friction_breakdown", t.DISPATCH)
            self.assertIn("friction_breakdown", {d["name"] for d in t.declarations()})


if __name__ == "__main__":
    unittest.main()
