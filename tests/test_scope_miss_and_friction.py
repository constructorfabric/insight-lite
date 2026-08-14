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


#: Flow reads board movement, and the default taxonomy maps no statuses — without this
#: every status resolves to "other", which has no position, so nothing is a direction.
_STAGES = {"Backlog": "backlog", "In progress": "in_progress",
           "To Verify": "qa", "Done": "done"}


@contextlib.contextmanager
def _tools():
    import semantic
    with TemporaryDirectory() as tmp:
        with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}), \
             patch.object(semantic, "stage_for",
                          lambda _c, raw: _STAGES.get(raw, "other")):
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
    # Board snapshots, because since v0.3 friction is computed from board MOVEMENT and
    # from nothing else: an item that never moves is in neither half of the ratio, and a
    # person with fewer than three moved items has no friction at all. ann's four items
    # move — three forward, one from testing back to development — so her share is 1/4,
    # while bob's five all advance cleanly, which gives a spread to take a median of.
    def snap(num, day, status):
        conn.execute(
            "INSERT INTO work_item_status (taken_at, date, item_id, project, item_type, "
            "repo, number, status_raw, title) "
            "VALUES (?, ?, ?, 'o/1', 'pull_request', 'o/alpha', ?, ?, ?)",
            (f"{day}T00:00:00Z", day, f"IT{num}", num, status, f"t{num}"))

    for n in (100, 101, 102):
        snap(n, "2026-06-12", "Backlog")
        snap(n, "2026-06-13", "In progress")
    snap(103, "2026-06-12", "To Verify")           # ann's one rewind
    snap(103, "2026-06-13", "In progress")
    for n in (200, 201, 202, 203, 204):
        snap(n, "2026-06-12", "In progress")
        snap(n, "2026-06-13", "Done")
    # `dan` is the contradiction between the two definitions of "flow". His PRs were CREATED
    # in MAY — before the window — so flow_report's cohort-gated `people` list (issues + PRs
    # created in the window, ≥3 to be listed) never lists him. But three of his items MOVE
    # inside the window, which is what person_flow and the score's flow pillar read, so both
    # of THOSE give him a friction reading. found must follow the pillar, not the cohort list.
    conn.execute("INSERT INTO person (login, name) VALUES ('dan', 'Dan')")
    for num in (900, 901, 902):
        conn.execute(
            "INSERT INTO pull_request (repo, number, org, author_login, created_at, "
            "merged_at, changed_files, review_count, is_revert, is_bot, is_migration) "
            "VALUES ('o/alpha', ?, 'o', 'dan', '2026-05-20T00:00:00Z', "
            "'2026-05-20T06:00:00Z', 3, 1, 0, 0, 0)", (num,))
        snap(num, "2026-06-12", "Backlog")
        snap(num, "2026-06-13", "In progress")
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
        """Since v0.3 friction is the backward SHARE of board moves, so the parts are the
        moves themselves — and unlike the rounded percentages they replaced, they add
        back up to the number."""
        with _tools() as t:
            out = t.friction_breakdown("ann", since=SINCE, until=UNTIL)
            self.assertTrue(out["found"])
            self.assertEqual(out["items_that_moved"], 4)
            fwd = out["parts"]["forward_moves"]
            back = out["parts"]["backward_moves"]
            self.assertEqual(back, 1, "one item went back to development")
            self.assertAlmostEqual(out["backward_share"], back / (fwd + back), places=2)
            self.assertIsNotNone(out["team_median"])

    def test_it_names_the_items_behind_the_number(self):
        with _tools() as t:
            worst = t.friction_breakdown("ann", since=SINCE, until=UNTIL)["worst_items"]
            self.assertTrue(worst, "an explanation with no items explains nothing")
            self.assertEqual(worst[0]["backward_moves"], 1, "worst first")
            self.assertIn("→", worst[0]["moves"])

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

    def test_the_reply_admits_what_the_snapshots_cannot_see(self):
        """Moves are reconstructed by diffing daily snapshots, so the count is a floor.
        A caller asked to explain a number must be told that, or it will present the
        number as exact."""
        with _tools() as t:
            note = t.friction_breakdown("ann", since=SINCE, until=UNTIL)["note"]
            self.assertIn("floor", note)
            self.assertIn("never moved", note)

    def test_a_bad_scope_is_refused_with_the_shape_of_a_scope(self):
        with _tools() as t:
            self.assertIn("org|element|repo|project",
                          t.friction_breakdown("ann", scope="person:ann")["error"])

    def test_items_that_moved_but_were_created_earlier_are_still_found(self):
        """The gate contradiction. `dan`'s three items MOVED in the window but his PRs were
        created in May, so the cohort-gated flow-report `people` list never lists him —
        while the score's flow pillar, read from board MOVEMENT, does. Deriving found from
        that list answered found=False and told him he needed '≥3 items that actually
        moved', which he had. found now comes from person_flow, the pillar's own source."""
        with _tools() as t:
            out = t.friction_breakdown("dan", since=SINCE, until=UNTIL)
            self.assertTrue(out["found"], "3 items moved in-window; the flow pillar has him")
            self.assertEqual(out["items_that_moved"], 3)
            self.assertEqual(out["backward_share"], 0.0, "all three only advanced")

    def test_it_carries_the_formula_and_the_direction(self):
        with _tools() as t:
            out = t.friction_breakdown("ann", since=SINCE, until=UNTIL)
            self.assertTrue(out["found"], "the fixture seeds board movement on purpose")
            self.assertIn("items_that_moved", out)
            self.assertIn("backward", out["formula"])
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
