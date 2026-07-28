"""Guards for the single source of truth behind is_bug / is_feature / is_epic.

The columns are a materialized projection of semantic.categorize_issue. collect.py
once ALSO guessed them from a config.yaml `labels:` map while writing the issue rows,
and the two silently diverged: 79e0cbb (2026-07-17) renamed is_user_story -> is_feature
in the code but not the config key, so the collector tested `"features" in cats`
against a category called `user_stories` and wrote is_feature=0 on every issue.
Nothing broke, because apply_issue_taxonomy() overwrote the column right
after — and that recovery sat behind a bare `except` that only printed a WARN. Lose
the taxonomy step and the report silently claims zero Bugs / Features / Epics.

These tests pin both halves: the pre-write value provably cannot change the outcome
(so removing the duplicate guess moved no numbers), and a taxonomy failure is loud.
"""
import ast
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import collect
import semantic_editor
import semantic_metrics
import store

# 'story' -> feature is the mapping config.yaml's dead `user_stories:` key claimed to
# make; here it comes from the taxonomy, which is the only thing that ever applied it.
_TAXONOMY = {"categories": {"labels": {"bug": "bug", "story": "feature", "epic": "epic"},
                            "types": {}},
             "stages": {"statuses": {}}, "ci": {"roles": {}}}


def _issue(number, login, labels, is_bug=0, is_feature=0):
    """An issue row shaped like collect.py's all-issues pass writes it."""
    return {"repo": "o/r", "number": number, "org": "o", "author_login": login,
            "created_at": "2026-07-01T00:00:00Z", "is_bug": is_bug,
            "is_feature": is_feature, "is_migration": 0, "is_bot": 0,
            "issue_type": "", "labels": labels, "title": f"i{number}"}


def _seed(conn):
    store.write_repos_dim(conn, [{"key": "o/r", "org": "o", "name": "r", "element": "E"}])
    semantic_editor.save(conn, "global", "", _TAXONOMY)


def _flags(conn):
    return {r["number"]: (r["is_bug"], r["is_feature"], r["is_epic"]) for r in
            conn.execute("SELECT number, is_bug, is_feature, is_epic FROM issue")}


class IssueCategoryPreWriteIsIrrelevantTest(unittest.TestCase):
    def test_label_pass_guess_cannot_change_the_resolved_columns(self):
        """The old label-derived guess and a plain 0 land on identical columns."""
        outcomes = []
        for guess in (True, False):
            with TemporaryDirectory() as tmp:
                with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                    conn = store.connect()
                    _seed(conn)
                    # `guess=True` reproduces the pre-fix writes: is_bug from the label
                    # map, is_feature stuck at 0 by the incomplete rename.
                    store.write_issues(conn, [
                        _issue(1, "alice", ["bug"], is_bug=int(guess)),
                        _issue(2, "alice", ["story"]),
                        _issue(3, "bob", ["epic"]),
                        _issue(4, "bob", ["wontfix"])])
                    people = {"alice": {"bugs": 0, "features": 0, "epics": 0},
                              "bob": {"bugs": 0, "features": 0, "epics": 0}}
                    n = collect.apply_issue_taxonomy(conn, people)
                    self.assertEqual(n, 4)
                    outcomes.append((_flags(conn), people))
        with_guess, without_guess = outcomes
        self.assertEqual(with_guess, without_guess)
        flags, people = without_guess
        self.assertEqual(flags, {1: (1, 0, 0), 2: (0, 1, 0), 3: (0, 0, 1), 4: (0, 0, 0)})
        self.assertEqual(people["alice"], {"bugs": 1, "features": 1, "epics": 0})
        self.assertEqual(people["bob"], {"bugs": 0, "features": 0, "epics": 1})

    def test_stale_person_counts_are_replaced_not_added_to(self):
        """Counts are assigned from the columns, so a stale/duplicated tally from an
        earlier pass cannot leak into the run blob."""
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                _seed(conn)
                store.write_issues(conn, [_issue(1, "alice", ["story"])])
                people = {"alice": {"bugs": 99, "features": 99, "epics": 99,
                                    "user_stories": 99}}
                collect.apply_issue_taxonomy(conn, people)
                self.assertEqual(people["alice"]["bugs"], 0)
                self.assertEqual(people["alice"]["features"], 1)
                self.assertEqual(people["alice"]["epics"], 0)

    def test_bots_and_migrations_are_excluded_from_person_counts(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                _seed(conn)
                bot = dict(_issue(2, "botty", ["story"]), is_bot=1)
                mig = dict(_issue(3, "alice", ["story"]), is_migration=1)
                store.write_issues(conn, [_issue(1, "alice", ["story"]), bot, mig])
                people = {"alice": {"bugs": 0, "features": 0, "epics": 0},
                          "botty": {"bugs": 0, "features": 0, "epics": 0}}
                collect.apply_issue_taxonomy(conn, people)
                # the columns still carry the resolved category for every row…
                self.assertEqual(_flags(conn)[2], (0, 1, 0))
                # …but only the one human, non-migration issue is credited
                self.assertEqual(people["alice"]["features"], 1)
                self.assertEqual(people["botty"]["features"], 0)


class TaxonomyFailureIsLoudTest(unittest.TestCase):
    def test_recategorize_failure_propagates_instead_of_zeroing_silently(self):
        """A failing taxonomy step must abort the run. write_issues() has already put
        0 in every category column, so swallowing this error is what shipped a report
        claiming zero Bugs / Features / Epics with only a WARN in the log."""
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                _seed(conn)
                store.write_issues(conn, [_issue(1, "alice", ["bug"]),
                                          _issue(2, "alice", ["story"])])
                people = {"alice": {"bugs": 0, "features": 0, "epics": 0}}
                with patch.object(semantic_metrics, "recategorize_issues",
                                  side_effect=RuntimeError("taxonomy exploded")):
                    with self.assertRaises(RuntimeError):
                        collect.apply_issue_taxonomy(conn, people)
                # the all-zero state the caller must never be allowed to publish
                self.assertEqual(set(_flags(conn).values()), {(0, 0, 0)})
                self.assertEqual(people["alice"], {"bugs": 0, "features": 0, "epics": 0})

    def test_collect_calls_the_taxonomy_step_outside_any_try(self):
        """Structural guard on main(): the call sits in no try block, so the failure
        cannot be quietly downgraded to a WARN again without this test noticing."""
        tree = ast.parse(Path(collect.__file__).read_text())
        main = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "main")
        parent = {c: p for p in ast.walk(main) for c in ast.iter_child_nodes(p)}
        calls = [n for n in ast.walk(main) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", "") == "apply_issue_taxonomy"]
        self.assertEqual(len(calls), 1, "main() must apply the taxonomy exactly once")
        node = calls[0]
        while node in parent:
            node = parent[node]
            self.assertNotIsInstance(node, ast.Try,
                                     "taxonomy failure must not be swallowed")


class NoStaticLabelConfigTest(unittest.TestCase):
    def test_collect_reads_no_labels_block_from_config(self):
        """cfg["labels"] had exactly one reader and it is gone. Re-adding one revives
        the second source of truth that made the is_feature bug invisible."""
        src = Path(collect.__file__).read_text()
        self.assertNotIn('cfg["labels"]', src)
        self.assertNotIn('cfg.get("labels"', src)

    def test_base_config_yaml_carries_no_labels_block(self):
        import yaml
        root = Path(collect.__file__).resolve().parent
        cfg = yaml.safe_load((root / "config.yaml").read_text()) or {}
        self.assertNotIn("labels", cfg)


if __name__ == "__main__":
    unittest.main()
