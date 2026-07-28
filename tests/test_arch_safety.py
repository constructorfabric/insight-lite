"""Phase-0 safety net for the planned architecture cleanup.

Two guards that make the later refactor safe to do:
  * TaxonomyParityTest — Phase 3 unified the two taxonomies: the is_bug/is_epic/
    is_feature columns are now a MATERIALIZED projection of the ONE resolver
    (semantic.categorize_issue), refreshed by recategorize_issues(). This asserts
    the columns agree with the live verdict (the old divergence is closed).
  * RenderSmokeTest — renders every report fragment + drill entity from a seeded
    store so a refactor that breaks a render/aggregation path fails CI. Structural,
    not byte-golden (the UI changes often): asserts no exception + that the
    data-period-panel wrappers the client swaps on are present.
"""
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

W = ("2026-06-01T00:00:00Z", "2026-07-01T00:00:00Z")


def _store(tmp):
    import store
    with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
        return store, store.connect()


class TaxonomyParityTest(unittest.TestCase):
    def test_columns_are_materialized_from_the_live_taxonomy(self):
        import store, semantic, semantic_metrics
        with TemporaryDirectory() as tmp:
            _, conn = _store(tmp)
            # one taxonomy: kind/bug->bug, kind/epic->epic, enhancement->feature
            store.write_override(conn, "semantic", "global::categories",
                {"labels": {"kind/bug": "bug", "kind/epic": "epic", "enhancement": "feature"},
                 "prefer_source": ["issue_type", "label", "title"],
                 "unmatched": "uncategorized"})
            # columns deliberately WRONG on insert (a bug labelled after last collect,
            # an epic mis-stored as a bug) — recategorize must correct them.
            conn.executemany(
                "INSERT INTO issue (repo,number,author_login,created_at,is_bot,is_migration,"
                "is_bug,is_feature,is_epic,labels,issue_type) VALUES (?,?,?,?,0,0,?,?,?,?,'')",
                [("o/r", 1, "al", "2026-06-05T00:00:00Z", 0, 0, 0, '["kind/bug"]'),
                 ("o/r", 2, "al", "2026-06-06T00:00:00Z", 1, 0, 0, '["kind/epic"]'),
                 ("o/r", 3, "al", "2026-06-07T00:00:00Z", 0, 0, 0, '["enhancement"]')])
            conn.commit()
            n = semantic_metrics.recategorize_issues(conn)
            self.assertEqual(n, 3)
            row = conn.execute("SELECT IFNULL(SUM(is_bug),0) b, IFNULL(SUM(is_epic),0) e, "
                               "IFNULL(SUM(is_feature),0) f FROM issue").fetchone()
            # the materialized columns now EQUAL the live categorize verdict — no gap.
            # is_feature holds the 'feature' category count.
            self.assertEqual((row["b"], row["e"], row["f"]), (1, 1, 1))
            resolved = semantic.resolve(semantic.load_layers(conn), {})["config"]
            live = {"bug": 0, "epic": 0, "feature": 0}
            for r in conn.execute("SELECT labels FROM issue"):
                cat = semantic.categorize_issue(resolved, json.loads(r[0] or "[]"))
                if cat in live:
                    live[cat] += 1
            self.assertEqual((row["b"], row["e"], row["f"]),
                             (live["bug"], live["epic"], live["feature"]))


class RenderSmokeTest(unittest.TestCase):
    def _seed(self, conn):
        conn.execute("INSERT INTO person (login,name,company,is_member) VALUES ('al','Al','Acme',1)")
        conn.execute("INSERT INTO commits (repo,sha,author_login,committed_at,meaningful_additions,is_bot) "
                     "VALUES ('o/r','a1c0ffee','al','2026-06-05T00:00:00Z',10,0)")
        conn.execute("INSERT INTO pull_request (repo,number,author_login,created_at,merged_at,state,"
                     "additions,changed_files,is_bot,is_migration) "
                     "VALUES ('o/r',1,'al','2026-06-05T00:00:00Z','2026-06-05T05:00:00Z','MERGED',10,2,0,0)")
        conn.execute("INSERT INTO issue (repo,number,author_login,created_at,is_bot,is_migration,is_bug) "
                     "VALUES ('o/r',2,'al','2026-06-06T00:00:00Z',0,0,1)")
        conn.execute("INSERT INTO review (repo,pr_number,reviewer_login,state,submitted_at) "
                     "VALUES ('o/r',1,'bob','APPROVED','2026-06-05T03:00:00Z')")
        conn.commit()

    def test_fragments_render_with_expected_panels(self):
        import store, semantic_metrics as sm, render
        with TemporaryDirectory() as tmp:
            _, conn = _store(tmp)
            self._seed(conn)
            period = render.render_period_fragment(
                store.aggregate(conn, *W, label="custom"), {"emails_by_login": {}})
            self.assertIn('data-period-panel="kpis"', period)
            deliv = render.render_delivery_fragment({"delivery": sm.window_block(conn, *W)})
            self.assertIn('data-period-panel="delivery-kpis"', deliv)
            flow = render.render_flow_fragment({"flow": sm.flow_report(conn, None, *W)})
            self.assertIn('data-period-panel="flow"', flow)

    def test_all_drill_entities_run(self):
        import store, semantic_metrics as sm
        with TemporaryDirectory() as tmp:
            _, conn = _store(tmp)
            self._seed(conn)
            for ent in ("commit", "pr", "issue"):
                self.assertIn("rows", store.drill(conn, ent, *W))
            self.assertIn("rows", store.people_drill(conn, *W))
            self.assertIn("rows", sm.drill_person_flow(conn, "al", since=W[0], until=W[1]))


if __name__ == "__main__":
    unittest.main()
