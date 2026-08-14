"""Two tools the chat transcript asked for loudest.

metric_definition, because metrics_catalog is the single biggest thing in a turn's payload
and every later round re-sends it: measured across six turns it was 72% to 100% of
everything the turn had fetched, up to 43,863 bytes, for 91 metrics when the question was
about one. Today's turn asking how one number is calculated never even called it — it went
to describe_schema, flow, person, list_items and sql_query trying to reverse-engineer the
formula from data, used all eight rounds, and produced nothing.

developer_score, because the score had no tool at all. The machinery is in store, the
questions in the log are "how do I raise my score", "what are my engagement metrics", "how
is my 0.14 friction calculated" — and every one of them had to be rebuilt from person(),
list_items(), flow() and raw SQL.
"""
import contextlib
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

SINCE, UNTIL = "2026-06-01", "2026-07-01"


@contextlib.contextmanager
def _tools(seed=True):
    with TemporaryDirectory() as tmp:
        with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
            import store
            conn = store.connect()
            if seed:
                _seed(conn)
            conn.close()
            import tooldefs
            yield tooldefs


#: Flow reads board movement; the default taxonomy maps no statuses, so patch it or every
#: status resolves to "other", which has no position and so is never a direction.
_STAGES = {"Backlog": "backlog", "In progress": "in_progress",
           "To Verify": "qa", "Done": "done"}


@contextlib.contextmanager
def _renorm_tools():
    """A window where the flow pillar is ACTIVE for the team but ABSENT for one person, so
    that person's flow weight is renormalised away. This is the shape where Σ over ALL
    pillars stops reproducing the score and scored_on/weight_gaps are the only honest way
    to check the arithmetic. alice and bob each move three board items in-window; carol
    moves none, so flow covers 2/3 of the team (≥50%, active) yet is null for carol."""
    import semantic
    with TemporaryDirectory() as tmp:
        with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}), \
             patch.object(semantic, "stage_for",
                          lambda _c, raw: _STAGES.get(raw, "other")):
            import store
            conn = store.connect()
            _seed(conn)                     # alice/bob/carol above the floor, June
            # alice's cohort PRs are 6100..6105, bob's 6200..6206 (see _seed's numbering).
            # Three of each advance Backlog -> In progress inside the window; carol's
            # 6300.. never appear on the board, so she is the person with no flow reading.
            def snap(num, day, status):
                conn.execute(
                    "INSERT INTO work_item_status (taken_at, date, item_id, project, "
                    "item_type, repo, number, status_raw, title) "
                    "VALUES (?, ?, ?, 'o/1', 'pull_request', 'o/r', ?, ?, ?)",
                    (f"{day}T00:00:00Z", day, f"IT{num}", num, status, f"t{num}"))
            for num in (6100, 6101, 6102, 6200, 6201, 6202):
                snap(num, "2026-06-12", "Backlog")
                snap(num, "2026-06-13", "In progress")
            conn.commit()
            conn.close()
            import tooldefs
            yield tooldefs


def _seed(conn, logins=("alice", "bob", "carol"), n=6, month="06", people=True):
    """`n` commits + `n` PRs each, above the score's activity floor of 5, so the board is
    non-empty and there is a team to be a median of. `month` so the window BEFORE the one
    under test can be seeded too: without activity there, store.score_delta has no previous
    board and returns None."""
    if people:
        conn.execute("INSERT INTO repo (key, org, name, element) VALUES "
                     "('o/r', 'o', 'r', 'Thing')")
    for idx, lg in enumerate(logins):
        if people:
            conn.execute("INSERT INTO person (login, name) VALUES (?, ?)", (lg, lg.title()))
        for i in range(n + idx):            # differing volumes so ranks differ
            day = f"2026-{month}-{10 + i:02d}"
            conn.execute(
                "INSERT INTO commits (repo, sha, committed_at, author_login, additions, "
                "meaningful_additions, is_spec, ai_marked, is_bot) "
                "VALUES ('o/r', ?, ?, ?, 10, 8, 0, 0, 0)",
                (f"{lg}{month}{i}", f"{day}T00:00:00Z", lg))
            conn.execute(
                "INSERT INTO pull_request (repo, number, org, author_login, created_at, "
                "merged_at, changed_files, review_count, is_revert, is_bot, is_migration) "
                "VALUES ('o/r', ?, 'o', ?, ?, ?, 3, 1, 0, 0, 0)",
                (int(month) * 1000 + 100 * (idx + 1) + i, lg,
                 f"{day}T00:00:00Z", f"{day}T06:00:00Z"))
    conn.commit()


class MetricDefinitionTest(unittest.TestCase):
    def test_a_plural_question_finds_the_singular_metric_first(self):
        """The regression. 'frictions' is not a substring of flow_friction_per_item, and
        falling through to a fuzzy name match put pr_median_additions at the TOP of the
        reply — a wrong formula first is worse than no answer, because it gets quoted."""
        with _tools(seed=False) as t:
            out = t.metric_definition("frictions")
            self.assertEqual(out["metrics"][0]["name"], "flow_friction_per_item")
            self.assertIn("friction", out["metrics"][0]["formula"].lower() + " "
                          + out["metrics"][0]["snippet"].lower())

    def test_an_exact_name_returns_exactly_that_metric(self):
        with _tools(seed=False) as t:
            out = t.metric_definition("flow_friction_per_item")
            self.assertEqual(out["matched"], "exact")
            self.assertEqual([m["name"] for m in out["metrics"]],
                             ["flow_friction_per_item"])

    def test_it_searches_descriptions_because_questions_arrive_in_words(self):
        with _tools(seed=False) as t:
            out = t.metric_definition("merge rate")
            self.assertTrue(out["metrics"], "a human phrase must find something")
            self.assertEqual(out["matched"], "description")

    def test_it_is_dramatically_smaller_than_the_whole_catalog(self):
        """The entire reason the tool exists: the catalog is re-sent on every later hop."""
        with _tools(seed=False) as t:
            one = len(json.dumps(t.metric_definition("flow_friction_per_item")))
            whole = len(json.dumps(t.metrics_catalog()))
            self.assertLess(one * 10, whole, f"{one}B vs {whole}B is not a saving")

    def test_a_nonsense_query_says_so_rather_than_guessing_confidently(self):
        with _tools(seed=False) as t:
            out = t.metric_definition("sausages")
            self.assertIn("may be right", out.get("matched", ""),
                          "a fuzzy fallback must be labelled as one")

    def test_an_empty_query_is_refused(self):
        with _tools(seed=False) as t:
            self.assertIn("required", t.metric_definition("  ")["error"])

    def test_the_internal_producing_function_is_not_leaked(self):
        with _tools(seed=False) as t:
            for m in t.metric_definition("friction")["metrics"]:
                self.assertNotIn("fn", m)


class DeveloperScoreTest(unittest.TestCase):
    def test_it_answers_with_the_number_its_band_and_the_rank(self):
        with _tools() as t:
            out = t.developer_score("carol", since=SINCE, until=UNTIL)
            self.assertTrue(out["scored"])
            self.assertIsInstance(out["score"], int)
            self.assertTrue(out["band"])
            self.assertEqual(out["of_scored"], 3)
            self.assertIn(out["rank"], (1, 2, 3))

    def test_every_signal_carries_the_team_median_and_its_direction(self):
        """"How do I raise it" is answerable only next to the median, and only if the
        reader knows which way is better — the direction lives nowhere else a caller can
        reach."""
        with _tools() as t:
            sigs = t.developer_score("alice", since=SINCE, until=UNTIL)["signals"]
            self.assertTrue(sigs)
            for s in sigs:
                self.assertIn("yours", s)
                self.assertIn("team_median", s)
                self.assertIsInstance(s["higher_is_better"], bool)
                self.assertTrue(s["label"])
                self.assertTrue(s["fmt"], "the renderer hint keeps 0.1423076923 from "
                                          "being quoted to sixteen digits")

    def test_it_does_not_return_the_board(self):
        """The anti-bloat invariant. Shipping 46 ranked rows with raw drivers on each is
        the metrics_catalog mistake in a new place; rank and the median carry the
        comparison, and top_contributors() exists for a ranking."""
        with _tools() as t:
            out = t.developer_score("alice", since=SINCE, until=UNTIL)
            self.assertNotIn("board", out)
            self.assertNotIn("by_login", out)
            self.assertLess(len(json.dumps(out)), 8000)

    def test_the_change_split_is_opt_in(self):
        with _tools() as t:
            plain = t.developer_score("alice", since=SINCE, until=UNTIL)
            self.assertNotIn("change", plain, "a second full scoring run must be asked for")
            with_change = t.developer_score("alice", since=SINCE, until=UNTIL,
                                            compare_previous=True)
            self.assertIn("change", with_change)

    def test_the_change_names_whose_movement_it_was(self):
        """This test asserted nothing until review caught it. The fixture seeded June only,
        the previous window is May, so there was no previous board, score_delta returned
        None, and the defensive `if change is not None` guard skipped every assertion — a
        guard written for safety that turned the test into a no-op. Both windows are seeded
        now and the delta is required to exist."""
        with _tools() as t:
            import store
            conn = store.connect()
            _seed(conn, month="05", people=False)      # the window before the one under test
            conn.close()
            change = t.developer_score("alice", since=SINCE, until=UNTIL,
                                       compare_previous=True)["change"]
            self.assertIsNotNone(change, "both windows are seeded, so a delta must exist")
            for k in ("prev", "now", "total", "team", "you"):
                self.assertIn(k, change)
            self.assertEqual(change["total"], change["team"] + change["you"],
                             "the split must add up to the move it splits")

    def test_an_unscored_login_is_told_why_and_where_to_look(self):
        with _tools() as t:
            out = t.developer_score("nobody", since=SINCE, until=UNTIL)
            self.assertFalse(out["scored"])
            self.assertIn("find_person", out["note"])
            self.assertEqual(out["min_activity"], 5)

    def test_a_missing_login_and_a_bad_scope_are_both_refused_clearly(self):
        with _tools() as t:
            self.assertIn("required", t.developer_score("")["error"])
            self.assertIn("org|element|repo|project",
                          t.developer_score("alice", scope="person:alice")["error"])

    def test_it_says_it_is_experimental(self):
        """The panel labels the score EXPERIMENTAL; an answer quoting it should be able to
        say the same thing rather than presenting it as settled."""
        with _tools() as t:
            self.assertIs(t.developer_score("alice", since=SINCE, until=UNTIL)["experimental"],
                          True)


class RenormalisedScoreTest(unittest.TestCase):
    """A person whose flow pillar was renormalised away. Without scored_on/weight_gaps in
    the payload, Σ weights_pct·pillar / Σ weights_pct over ALL pillars does not reproduce
    `score` — the reader then either says the arithmetic is wrong or reads flow=None as a
    scored zero. The payload has to say which pillars the mean was actually taken over."""

    def test_scored_on_and_weight_gaps_are_present(self):
        with _renorm_tools() as t:
            out = t.developer_score("carol", since=SINCE, until=UNTIL)
            self.assertTrue(out["scored"])
            self.assertIn("scored_on", out)
            self.assertIn("weight_gaps", out)
            self.assertIn("flow", out["weight_gaps"], "carol has no board movement")
            self.assertNotIn("flow", out["scored_on"])

    def test_the_score_is_the_weighted_mean_over_scored_on(self):
        with _renorm_tools() as t:
            out = t.developer_score("carol", since=SINCE, until=UNTIL)
            w, pil, so = out["weights_pct"], out["pillars"], out["scored_on"]
            den = sum(w[p] for p in so)
            recon = round(sum(w[p] * pil[p] for p in so) / den)
            self.assertEqual(recon, out["score"],
                             "Σ over scored_on must reproduce the score")
            # The naive mean over ALL pillars (flow counted as a 0) does NOT — which is the
            # whole reason scored_on/weight_gaps have to travel with the number.
            allp = so + out["weight_gaps"]
            naive = round(sum(w[p] * (pil[p] or 0) for p in allp) / sum(w[p] for p in allp))
            self.assertLess(naive, out["score"],
                            "renormalising the flow weight away is what lifts the score")

    def test_how_it_works_explains_the_renormalisation(self):
        with _renorm_tools() as t:
            hiw = t.developer_score("carol", since=SINCE, until=UNTIL)["how_it_works"]
            self.assertIn("scored_on", hiw)
            self.assertIn("weight_gaps", hiw)


class PreviousWindowTest(unittest.TestCase):
    def test_it_is_the_equal_span_immediately_before(self):
        with _tools(seed=False) as t:
            lo, hi = t._previous_window("2026-06-11T00:00:00Z", "2026-06-21T00:00:00Z")
            self.assertEqual(lo, "2026-06-01T00:00:00Z")
            self.assertEqual(hi, "2026-06-10T23:59:59Z")

    def test_it_declines_a_window_that_is_not_one(self):
        with _tools(seed=False) as t:
            self.assertIsNone(t._previous_window("2026-06-21T00:00:00Z",
                                                 "2026-06-11T00:00:00Z"))
            self.assertIsNone(t._previous_window("not a date", "2026-06-11T00:00:00Z"))


class RegistrationTest(unittest.TestCase):
    def test_both_are_dispatchable_and_declared(self):
        with _tools(seed=False) as t:
            names = {d["name"] for d in t.declarations()}
            for fn in ("metric_definition", "developer_score"):
                self.assertIn(fn, t.DISPATCH, fn)
                self.assertIn(fn, names, fn)

    def test_the_catalog_tool_points_at_the_targeted_one(self):
        """Leaving both with equal billing is how the expensive one keeps being chosen."""
        with _tools(seed=False) as t:
            doc = next(d for d in t.declarations() if d["name"] == "metric_definition")
            self.assertIn("metrics_catalog", doc["description"])


if __name__ == "__main__":
    unittest.main()
