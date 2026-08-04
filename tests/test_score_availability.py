"""The Developer-score panel must never fail silently.

Before this, server.py built the panel inside `except Exception: score = None` and
render.py dropped the block whenever the board was empty — so a raising builder and
a window with nobody in it produced the SAME person page, with no log line anywhere.
To a reader that says "this person has no score"; to an operator it says nothing at
all. That is the July 2026 dead-collector failure shape (server.data_freshness /
alert.py) at request scope.

Three cases, which the code now has to tell apart:
  * the score BUILDS   → identical payload to before, `scoreUnavailable` is None
  * the builder RAISES → panel gone, reason "error", traceback in the server log
  * the window is EMPTY→ panel gone, reason "no_data", nothing logged (not a fault)

Handler-level tests drive server.Handler._developer_score_block against a seeded
temp store (no socket: the method needs only self.headers), so the real store
queries run; render-level tests drive the pure gate render._score_availability
through render.person_json.
"""
import contextlib
import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import render
import server

SINCE, UNTIL = "2026-06-01T00:00:00Z", "2026-07-01T00:00:00Z"


def _handler():
    """A Handler with no socket — _developer_score_block only reads self.headers
    (for the proxy identity), so bypassing BaseHTTPRequestHandler.__init__ is enough
    and keeps the test off the network."""
    h = server.Handler.__new__(server.Handler)
    h.headers = {}
    return h


def _seed(conn, logins=("alice", "bob"), n=6):
    """`n` commits + `n` PRs each — above store._SCORE_MIN_ACTIVITY (5), so these
    people are eligible and the board is non-empty."""
    for idx, lg in enumerate(logins):
        conn.execute("INSERT INTO person (login, name) VALUES (?, ?)", (lg, lg.title()))
        for i in range(n):
            day = f"2026-06-{10 + i:02d}"
            conn.execute(
                "INSERT INTO commits (repo, sha, committed_at, author_login, additions, "
                "meaningful_additions, is_spec, ai_marked, is_bot) "
                "VALUES ('o/r', ?, ?, ?, 10, 8, 0, 0, 0)",
                (f"{lg}{i}", f"{day}T00:00:00Z", lg))
            conn.execute(
                "INSERT INTO pull_request (repo, number, org, author_login, created_at, "
                "merged_at, changed_files, review_count, is_revert, is_bot, is_migration) "
                "VALUES ('o/r', ?, 'o', ?, ?, ?, 3, 1, 0, 0, 0)",
                (100 * (idx + 1) + i, lg, f"{day}T00:00:00Z", f"{day}T06:00:00Z"))
    conn.commit()


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


class ScoreBlockBuilderTest(unittest.TestCase):
    """server.Handler._developer_score_block — the (score, unavailable) contract."""

    def test_builds_and_says_nothing_when_the_window_has_ranked_people(self):
        with _store() as conn:
            _seed(conn)
            log = io.StringIO()
            with contextlib.redirect_stderr(log):
                score, unavailable = _handler()._developer_score_block(
                    conn, "alice", SINCE, UNTIL)
        self.assertIsNone(unavailable)
        self.assertIsNotNone(score)
        self.assertEqual({r["login"] for r in score["board"]}, {"alice", "bob"})
        self.assertEqual(score["self"]["login"], "alice")
        self.assertEqual(score["n_eligible"], 2)
        self.assertFalse(score["is_self_view"])          # no proxy identity in headers
        # a healthy build is quiet. Asserted as "nothing was REPORTED AS DEGRADED"
        # rather than "stderr is empty": this captures the whole stream, and an
        # unrelated ResourceWarning from a garbage collection that happens to land
        # inside the block would otherwise fail the test on timing alone.
        self.assertNotIn("degraded", log.getvalue())

    def test_a_raising_builder_is_reported_and_logged_with_a_traceback(self):
        with _store() as conn:
            log = io.StringIO()
            with contextlib.redirect_stderr(log), \
                    patch("store.developer_scores", side_effect=RuntimeError("boom")):
                score, unavailable = _handler()._developer_score_block(
                    conn, "alice", SINCE, UNTIL)
        self.assertIsNone(score)
        self.assertEqual(unavailable["reason"], "error")
        self.assertTrue(unavailable["detail"])
        # the operator's copy: where, what, and a traceback to act on
        out = log.getvalue()
        self.assertIn("developer score for alice", out)
        self.assertIn("2026-06-01→2026-07-01", out)
        self.assertIn("RuntimeError: boom", out)
        self.assertIn("Traceback (most recent call last)", out)
        # ...but no traceback in what the page will show
        self.assertNotIn("Traceback", unavailable["detail"])
        self.assertNotIn("boom", unavailable["detail"])

    def test_an_empty_window_is_not_an_error(self):
        """Nothing collected → the builder SUCCEEDS with an empty board. It must not
        be dressed up as a failure; render turns this into reason "no_data"."""
        with _store() as conn:
            log = io.StringIO()
            with contextlib.redirect_stderr(log):
                score, unavailable = _handler()._developer_score_block(
                    conn, "alice", SINCE, UNTIL)
        self.assertIsNone(unavailable)
        self.assertEqual(score["board"], [])
        self.assertEqual(score["n_eligible"], 0)
        self.assertNotIn("degraded", log.getvalue())      # empty is not a fault

    def test_the_person_endpoint_uses_the_shared_builder(self):
        """There is one person endpoint since the legacy /api/person fragment went with
        the monolith, and it must still go through the shared builder rather than growing
        its own copy of the score logic.

        Two doors now, not one. developer_scores costs seconds, so the handler reaches it
        through _cached_scores; a direct call from the handler would be correct and slow,
        which is the kind of regression nothing else notices. So: the builder is called
        once, the handler never calls the scorer itself, and the module has exactly one
        place that does."""
        import inspect
        handler = inspect.getsource(server.Handler)
        module = inspect.getsource(server)
        self.assertEqual(handler.count("self._developer_score_block("), 1)
        self.assertEqual(handler.count("store.developer_scores("), 0,
                         "the handler must go through _cached_scores, not the scorer")
        self.assertEqual(module.count("store.developer_scores("), 1,
                         "one door to the scorer, and it is the cache")


# --- render side: the gate that decides whether the panel is drawn -------------

def _dash(score=None, unavailable=None):
    """A person payload with real activity, so the dashboard is not the `empty`
    shape and the score gate is actually reached."""
    return {
        "login": "alice",
        "profile": {"login": "alice",
                    "totals": {"commits": 20, "meaningful_additions": 100, "prs": 3},
                    "shares": {"commits": 10.0}, "repos": [], "split": {"types": [], "total": 0},
                    "mix": {"code": 1, "specs": 0}},
        "alltime": {"name": "Alice A", "commits": 20},
        "heat": [], "weekly": {}, "emails": "", "gh_profile": {},
        "score": score, "score_unavailable": unavailable,
    }


def _board_score(**over):
    sc = {"self": {"login": "alice", "score": 70, "rank": 1}, "board": [{"login": "alice"}],
          "weights": {"engagement": 20}, "n_eligible": 1, "n_ranked": 1,
          "active_pillars": ["engagement"], "team_medians": {}, "min_activity": 5,
          "is_self_view": False}
    sc.update(over)
    return sc


_META = {"org": "acme", "all_time": True, "window_start": "2008-01-01", "lookback_days": 30,
         "generated": "2026-07-28T09:00:00Z", "all_label": "All-time", "person": "alice",
         "window_labels": ["30d", "all"]}


class ScoreAvailabilityJsonTest(unittest.TestCase):
    def _dashboard(self, **kw):
        return render.person_json(_dash(**kw), _META)["dashboard"]

    def test_working_score_passes_through_untouched(self):
        sc = _board_score()
        d = self._dashboard(score=sc)
        self.assertIs(d["score"], sc)                    # not rebuilt, not reshaped
        self.assertIsNone(d["scoreUnavailable"])         # nothing for the page to say

    def test_working_case_gains_no_other_field(self):
        """The React views were migrated under a screenshot-diff gate: the working
        case must keep exactly the fields it had, plus the one null marker."""
        d = self._dashboard(score=_board_score())
        self.assertEqual(set(d), {
            "empty", "login", "header", "ghProfile", "kpis", "heat", "weekly", "repos",
            "repoTypes", "codeSpecs", "elements", "workType", "impact", "score",
            "scoreUnavailable"})

    def test_builder_error_is_reported_not_hidden(self):
        d = self._dashboard(score=None, unavailable={"reason": "error", "detail": "it broke"})
        self.assertIsNone(d["score"])
        self.assertEqual(d["scoreUnavailable"], {"reason": "error", "detail": "it broke"})

    def test_empty_board_reads_as_no_data_with_the_activity_floor(self):
        d = self._dashboard(score=_board_score(board=[], self=None, n_eligible=0, n_ranked=0))
        self.assertIsNone(d["score"])
        self.assertEqual(d["scoreUnavailable"]["reason"], "no_data")
        self.assertIn("5-commits-and-PRs", d["scoreUnavailable"]["detail"])

    def test_no_score_at_all_reads_as_no_data(self):
        d = self._dashboard()
        self.assertEqual(d["scoreUnavailable"]["reason"], "no_data")
        self.assertIn("no score data", d["scoreUnavailable"]["detail"])

    def test_error_wins_over_the_no_data_wording(self):
        """An error must never be softened into "no data" — that was the whole bug."""
        d = self._dashboard(score=_board_score(board=[], n_eligible=0),
                            unavailable={"reason": "error", "detail": "it broke"})
        self.assertEqual(d["scoreUnavailable"]["reason"], "error")

    def test_person_with_no_activity_stays_the_empty_shape(self):
        dash = _dash(score=_board_score())
        dash["profile"] = {"login": "ghost", "totals": {}, "shares": {}, "repos": [],
                           "split": {"types": [], "total": 0}, "mix": {}}
        dash["alltime"] = {}
        d = render.person_json(dash, _META)["dashboard"]
        self.assertTrue(d["empty"])
        self.assertNotIn("scoreUnavailable", d)          # no panel, no note, no page

    def test_gate_never_touches_the_db(self):
        """render.person_json is documented as pure — the availability gate must not
        have quietly turned it into a DB caller."""
        import inspect
        src = inspect.getsource(render._score_availability)
        for forbidden in ("store.", "connect(", "execute("):
            self.assertNotIn(forbidden, src)


if __name__ == "__main__":
    unittest.main()
