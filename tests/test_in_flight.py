"""Guards for work-in-flight (store.in_flight + its Flow JSON block + drills).

Design decisions from docs/superpowers/plans/2026-07-28-work-in-flight.md that are
easy to undo by accident, so they are pinned here:
  · in_flight takes no since/until — it is a point-in-time quantity
  · it is never folded into commit/LOC/delivered counters
  · every tile's number equals the drill that opens behind it
  · the block survives flow_json's has_data gate (open PRs exist regardless of
    whether anything was created in the window)
"""
import inspect
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import render
import store

ALL = ("2008-01-01T00:00:00Z", "2099-01-01T00:00:00Z")


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pr(conn, number, *, age_days, state="OPEN", login="alice", draft=0,
        reviews=0, merged_at=None, is_bot=0):
    conn.execute(
        "INSERT INTO pull_request (repo, number, org, author_login, created_at, "
        "merged_at, state, is_draft, review_count, is_bot, is_migration, title) "
        "VALUES ('o/r', ?, 'o', ?, ?, ?, ?, ?, ?, ?, 0, 'p')",
        (number, login, _ago(age_days), merged_at, state, draft, reviews, is_bot))
    conn.commit()


class SignatureTest(unittest.TestCase):
    def test_in_flight_cannot_be_period_scoped(self):
        """The decision is structural, not a convention: no since/until parameter
        exists, so a later edit cannot quietly make the period apply."""
        params = set(inspect.signature(store.in_flight).parameters)
        self.assertEqual(params, {"conn", "repos"})


class InFlightShapeTest(unittest.TestCase):
    def _seed(self, tmp):
        with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
            conn = store.connect()
        _pr(conn, 1, age_days=2)                              # fresh, unreviewed
        _pr(conn, 2, age_days=20, reviews=3)                  # 7-30d, reviewed
        _pr(conn, 3, age_days=45, draft=1)                    # 30-90d, draft
        _pr(conn, 4, age_days=200, login="bob")               # >90d, bob
        _pr(conn, 5, age_days=10, state="MERGED",
            merged_at=_ago(1))                                # merged -> excluded
        _pr(conn, 6, age_days=10, state="CLOSED")             # abandoned -> excluded
        _pr(conn, 7, age_days=10, is_bot=1)                   # bot -> excluded
        return conn

    def test_counts_only_open_non_bot_prs(self):
        with TemporaryDirectory() as tmp:
            d = store.in_flight(self._seed(tmp))
            self.assertEqual(d["n"], 4)
            self.assertEqual(d["drafts"], 1)
            self.assertEqual(d["unreviewed"], 3)      # 1, 3, 4 (2 has reviews)
            self.assertFalse(d["period_scoped"])

    def test_bands_partition_the_open_set(self):
        with TemporaryDirectory() as tmp:
            d = store.in_flight(self._seed(tmp))
            self.assertEqual(sum(b["n"] for b in d["bands"]), d["n"])
            by = {b["key"]: b["n"] for b in d["bands"]}
            self.assertEqual((by["d7"], by["d30"], by["d90"], by["d90p"]), (1, 1, 1, 1))

    def test_ages_and_people(self):
        with TemporaryDirectory() as tmp:
            d = store.in_flight(self._seed(tmp))
            self.assertGreaterEqual(d["oldest_age_d"], 199)
            logins = [p["login"] for p in d["people"]]
            self.assertEqual(logins[0], "bob")            # oldest first
            alice = next(p for p in d["people"] if p["login"] == "alice")
            self.assertEqual((alice["n"], alice["drafts"], alice["unreviewed"]), (3, 1, 2))

    def test_repo_slice_applies_even_though_the_period_does_not(self):
        with TemporaryDirectory() as tmp:
            conn = self._seed(tmp)
            self.assertEqual(store.in_flight(conn, ["o/r"])["n"], 4)
            self.assertEqual(store.in_flight(conn, ["other/repo"])["n"], 0)
            self.assertEqual(store.in_flight(conn, [])["n"], 0)

    def test_empty_db_is_zeros_not_a_crash(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                d = store.in_flight(store.connect())
            self.assertEqual(d["n"], 0)
            self.assertIsNone(d["median_age_d"])
            self.assertIsNone(d["oldest_age_d"])
            self.assertEqual(d["people"], [])


class TileMatchesDrillTest(unittest.TestCase):
    """A tile whose drill disagrees with it is worse than no drill."""

    def test_every_tile_equals_its_drill(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
            for i, (age, draft, reviews) in enumerate(
                    [(2, 0, 0), (20, 0, 2), (45, 1, 0), (200, 0, 0), (120, 1, 1)], start=1):
                _pr(conn, i, age_days=age, draft=draft, reviews=reviews)
            d = store.in_flight(conn)

            self.assertEqual(
                d["n"], store.drill(conn, "pr", *ALL, pr_state="open", limit=1)["total"])
            self.assertEqual(
                d["unreviewed"],
                store.drill(conn, "pr", *ALL, pr_state="open_unreviewed", limit=1)["total"])
            self.assertEqual(
                d["drafts"],
                store.drill(conn, "pr", *ALL, pr_state="open", flag="is_draft",
                            limit=1)["total"])
            # "open over N days" drills as created_at <= stale_before
            aging = sum(b["n"] for b in d["bands"] if b["key"] in ("d90", "d90p"))
            self.assertEqual(
                aging,
                store.drill(conn, "pr", ALL[0], d["stale_before"] + "T23:59:59Z",
                            pr_state="open", limit=1)["total"])

    def test_merged_and_abandoned_drills_are_untouched(self):
        """The new pr_state values must not disturb the existing two."""
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
            _pr(conn, 1, age_days=5)
            _pr(conn, 2, age_days=5, state="MERGED", merged_at=_ago(1))
            _pr(conn, 3, age_days=5, state="CLOSED")
            self.assertEqual(
                store.drill(conn, "pr", *ALL, pr_state="merged", limit=1)["total"], 1)
            self.assertEqual(
                store.drill(conn, "pr", *ALL, pr_state="abandoned", limit=1)["total"], 1)
            self.assertEqual(
                store.drill(conn, "pr", *ALL, pr_state="open", limit=1)["total"], 1)


class FlowJsonTest(unittest.TestCase):
    def test_block_survives_the_no_cohort_gate(self):
        """flow_json short-circuits to {hasData: false} when nothing was created in
        the window. Open PRs are independent of that, so the block must still be
        there — hiding a real number behind an unrelated flag is the bug pattern
        this whole feature exists to undo."""
        inf = {"period_scoped": False, "n": 3, "drafts": 1, "unreviewed": 2,
               "median_age_d": 12, "oldest_age_d": 40, "stale_before": "2026-06-28",
               "stale_days": 30,
               "bands": [{"key": "d7", "label": "< 7 days", "n": 1}],
               "people": [{"login": "alice", "n": 3, "drafts": 1, "unreviewed": 2,
                           "oldest_age_d": 40}]}
        env = render.flow_json({"flow": {"has_data": False}, "in_flight": inf}, {})
        self.assertEqual(env["flow"], {"hasData": False})
        self.assertEqual(env["inFlight"]["n"], 3)
        self.assertEqual(env["inFlight"]["unreviewed"], 2)
        self.assertEqual(env["inFlight"]["staleBefore"], "2026-06-28")
        self.assertFalse(env["inFlight"]["periodScoped"])
        self.assertEqual(env["inFlight"]["people"][0]["oldestAgeD"], 40)

    def test_missing_in_flight_degrades_to_zeros(self):
        env = render.flow_json({"flow": {"has_data": False}}, {})
        self.assertEqual(env["inFlight"]["n"], 0)
        self.assertEqual(env["inFlight"]["people"], [])


class NotSummedIntoDeliveredTest(unittest.TestCase):
    def test_open_prs_do_not_reach_the_delivered_counters(self):
        """The invariant the plan calls non-negotiable: in-flight work must not move
        a delivered number. An open PR contributes to in_flight and to nothing else."""
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
            before = store.aggregate(conn, *ALL)
            _pr(conn, 99, age_days=3)
            after = store.aggregate(conn, *ALL)
            self.assertEqual(store.in_flight(conn)["n"], 1)
            for key in ("commits", "meaningful_additions", "prs_merged"):
                self.assertEqual(before.get(key), after.get(key), key)


if __name__ == "__main__":
    unittest.main()


def _close_event(conn, number, actor, when_days_ago):
    conn.execute(
        "INSERT OR REPLACE INTO timeline_event (repo, item_type, number, event, "
        "actor_login, created_at) VALUES ('o/r','pull_request',?,'closed',?,?)",
        (number, actor, _ago(when_days_ago)))
    conn.commit()


class AbandonReasonTest(unittest.TestCase):
    """The five buckets, exercised with synthetic closing events — the only way to
    cover them, since a real DB's timeline coverage depends on the collection window."""

    def _seed(self, tmp):
        with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
            conn = store.connect()
        # (number, author, reviews, draft, closer)
        cases = [
            (1, "alice", 2, 0, "alice"),   # withdrawn_reviewed
            (2, "alice", 0, 0, "alice"),   # withdrawn_unreviewed
            (3, "bob", 1, 1, "bob"),       # draft wins over everything
            (4, "bob", 3, 0, "maint"),     # rejected
            (5, "carol", 0, 0, "maint"),   # swept
        ]
        for num, who, reviews, draft, closer in cases:
            _pr(conn, num, age_days=40, state="CLOSED", login=who,
                draft=draft, reviews=reviews)
            conn.execute("UPDATE pull_request SET closed_at=? WHERE number=?",
                         (_ago(5), num))
            _close_event(conn, num, closer, 5)
        conn.commit()
        return conn

    def test_each_case_lands_in_its_own_bucket(self):
        with TemporaryDirectory() as tmp:
            d = store.abandoned_prs(self._seed(tmp), *ALL)
            got = {r["key"]: r["n"] for r in d["reasons"] if r["n"]}
            self.assertEqual(got, {"withdrawn_reviewed": 1, "withdrawn_unreviewed": 1,
                                   "draft": 1, "rejected": 1, "swept": 1})
            self.assertEqual(d["n"], 5)

    def test_reason_drill_matches_every_bucket(self):
        with TemporaryDirectory() as tmp:
            conn = self._seed(tmp)
            for r in store.abandoned_prs(conn, *ALL)["reasons"]:
                got = store.drill(conn, "pr", *ALL, abandon_reason=r["key"],
                                  limit=1)["total"]
                self.assertEqual(r["n"], got, r["key"])

    def test_a_reclosed_pr_is_counted_once(self):
        """A PR closed, reopened and closed again has two 'closed' events. Joining
        both reports more rows than PRs and inflates every bucket — the bug this
        was written after hitting."""
        with TemporaryDirectory() as tmp:
            conn = self._seed(tmp)
            # an earlier close by someone else; the LAST one (alice, above) must win
            conn.execute(
                "INSERT INTO timeline_event (repo, item_type, number, event, "
                "actor_login, created_at) VALUES ('o/r','pull_request',1,'closed',"
                "'maint',?)", (_ago(30),))
            conn.commit()
            d = store.abandoned_prs(conn, *ALL)
            self.assertEqual(d["n"], 5)                       # not 6
            self.assertEqual(sum(r["n"] for r in d["reasons"]), 5)
            got = {r["key"]: r["n"] for r in d["reasons"] if r["n"]}
            self.assertEqual(got["withdrawn_reviewed"], 1)    # last actor, not first
            self.assertEqual(got.get("rejected"), 1)          # only PR 4

    def test_an_issue_with_the_same_number_does_not_leak_in(self):
        """timeline_event's PK omits item_type, so queries must filter on it."""
        with TemporaryDirectory() as tmp:
            conn = self._seed(tmp)
            conn.execute(
                "INSERT INTO timeline_event (repo, item_type, number, event, "
                "actor_login, created_at) VALUES ('o/r','issue',5,'closed','someone',?)",
                (_ago(1),))
            conn.commit()
            d = store.abandoned_prs(conn, *ALL)
            got = {r["key"]: r["n"] for r in d["reasons"] if r["n"]}
            self.assertEqual(got["swept"], 1)     # PR 5 keeps its own closer

    def test_missing_close_event_is_unknown_not_a_guess(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
            _pr(conn, 9, age_days=10, state="CLOSED")
            conn.execute("UPDATE pull_request SET closed_at=? WHERE number=9", (_ago(2),))
            conn.commit()
            d = store.abandoned_prs(conn, *ALL)
            self.assertEqual([r["key"] for r in d["reasons"] if r["n"]], ["unknown"])


class AbandonRateTest(unittest.TestCase):
    def test_rate_is_measured_against_closures_not_openings(self):
        """A window that clears a merge backlog must not read as a quality collapse."""
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
            # opened long ago, merged inside the window
            for n in (1, 2, 3):
                _pr(conn, n, age_days=300, state="MERGED", merged_at=_ago(2))
            _pr(conn, 4, age_days=300, state="CLOSED")
            conn.execute("UPDATE pull_request SET closed_at=? WHERE number=4", (_ago(2),))
            conn.commit()
            since = _ago(10)
            d = store.abandoned_prs(conn, since, _ago(0))
            self.assertEqual((d["n"], d["merged"], d["closed_total"]), (1, 3, 4))
            self.assertEqual(d["rate_pct"], 25.0)

    def test_window_excludes_closures_outside_it(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
            _pr(conn, 1, age_days=400, state="CLOSED")
            conn.execute("UPDATE pull_request SET closed_at=? WHERE number=1", (_ago(200),))
            conn.commit()
            self.assertEqual(store.abandoned_prs(conn, _ago(10), _ago(0))["n"], 0)
            self.assertEqual(store.abandoned_prs(conn, _ago(300), _ago(0))["n"], 1)


class StaleAndSizeTest(unittest.TestCase):
    def test_stale_unreviewed_is_ranked_by_wait(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
            _pr(conn, 1, age_days=3)                    # too fresh
            _pr(conn, 2, age_days=20)                   # stale, unreviewed
            _pr(conn, 3, age_days=90)                   # stale, unreviewed, worse
            _pr(conn, 4, age_days=60, reviews=2)        # reviewed -> not waiting
            d = store.in_flight(conn)
            self.assertEqual(d["stale_unreviewed_n"], 2)
            self.assertEqual([i["number"] for i in d["stale_unreviewed"]], [3, 2])
            self.assertNotIn("never_asked_n", d)        # not derivable, so not reported

    def test_size_is_a_shape_not_a_sum(self):
        """One giant PR must not define the number: median and p90 survive it."""
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
            for n, adds in enumerate([10, 20, 30, 40, 500000], start=1):
                _pr(conn, n, age_days=5)
                conn.execute("UPDATE pull_request SET additions=?, changed_files=2 "
                             "WHERE number=?", (adds, n))
            conn.commit()
            s = store.in_flight(conn)["size"]
            self.assertEqual(s["median_additions"], 30)          # unmoved by the outlier
            self.assertTrue(s["raw_lines"])                      # honest about the filter
            self.assertEqual(s["biggest"][0]["additions"], 500000)
            self.assertNotIn("total_additions", s)               # deliberately absent
