"""Guards for store.connect()'s lightweight schema migrations.

Every per-run table is created with CREATE TABLE IF NOT EXISTS, so a column rename
in _SCHEMA is a no-op on an existing DB — the writer then fails on every run. That
is exactly how the user_stories → features rename broke the nightly collect with
"table person_runs has no column named features". These tests pin the two halves:
a pre-rename DB is upgraded in place, and the writer column lists always match the
tables connect() hands back.
"""
import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import store

# person_runs exactly as it shipped before the 2026-07 features rename.
_LEGACY_PERSON_RUNS = """
CREATE TABLE person_runs (
    date                 TEXT NOT NULL,
    login                TEXT NOT NULL,
    name                 TEXT,
    company              TEXT,
    is_member            INTEGER,
    commits              INTEGER,
    meaningful_additions INTEGER,
    prs_opened           INTEGER,
    prs_merged           INTEGER,
    specs                INTEGER,
    bugs                 INTEGER,
    user_stories         INTEGER,
    reviews_given        INTEGER,
    approvals_given      INTEGER,
    ai_commits           INTEGER,
    cpt_lines            INTEGER,
    surviving_code_human INTEGER,
    surviving_code_ai    INTEGER,
    surviving_spec_human INTEGER,
    median_ttm_h         REAL,
    total_activity       INTEGER,
    PRIMARY KEY (date, login)
);
"""


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


class PersonRunsFeaturesRenameTest(unittest.TestCase):
    def test_legacy_user_stories_column_is_renamed_and_data_preserved(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            legacy = sqlite3.connect(path)
            legacy.executescript(_LEGACY_PERSON_RUNS)
            legacy.execute("INSERT INTO person_runs (date, login, commits, bugs, "
                           "user_stories) VALUES ('2026-07-01', 'alice', 5, 2, 7)")
            legacy.commit()
            legacy.close()

            with patch.dict(os.environ, {"REPORT_DB": str(path)}):
                conn = store.connect()
            cols = _cols(conn, "person_runs")
            self.assertIn("features", cols)
            self.assertNotIn("user_stories", cols)
            row = conn.execute("SELECT commits, bugs, features FROM person_runs "
                               "WHERE login='alice'").fetchone()
            self.assertEqual((row["commits"], row["bugs"], row["features"]), (5, 2, 7))

    def test_upsert_run_succeeds_against_a_migrated_legacy_db(self):
        """The failure mode itself: collect calls upsert_run right after connect()."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            legacy = sqlite3.connect(path)
            legacy.executescript(_LEGACY_PERSON_RUNS)
            legacy.commit()
            legacy.close()

            with patch.dict(os.environ, {"REPORT_DB": str(path)}):
                conn = store.connect()
                store.upsert_run(conn, {
                    "generated_at": "2026-07-02T00:00:00Z", "lookback_days": 30,
                    "org": "acme",
                    "people": {"bob": {"commits": 3, "bugs": 1, "features": 4}},
                    "repos": {},
                })
            row = conn.execute("SELECT features FROM person_runs "
                               "WHERE login='bob'").fetchone()
            self.assertEqual(row["features"], 4)


class LegacyBlobUpsertTest(unittest.TestCase):
    def test_pre_rename_blob_keeps_its_feature_count(self):
        """reindex/reconfig re-upsert the STORED blob on every identity or config
        save. A pre-rename blob has no 'features' key, which would write NULL over
        the real count — the legacy key has to be read instead."""
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                blob = {
                    "generated_at": "2026-07-11T16:10:46Z", "lookback_days": 30,
                    "org": "acme",
                    "people": {"carol": {"commits": 9, "bugs": 2, "user_stories": 6}},
                    "repos": {},
                }
                store.upsert_run(conn, blob)
                row = conn.execute("SELECT features FROM person_runs "
                                   "WHERE login='carol'").fetchone()
                self.assertEqual(row["features"], 6)
                # a second pass over the now-normalised stored blob is stable
                store.upsert_run(conn, store.read_latest_run(conn))
                row = conn.execute("SELECT features FROM person_runs "
                                   "WHERE login='carol'").fetchone()
                self.assertEqual(row["features"], 6)


class WriterColumnsExistTest(unittest.TestCase):
    def test_per_run_writer_columns_are_all_present_on_a_fresh_db(self):
        """PERSON_COLS/REPO_COLS drive the INSERT — a name added to one list without
        the matching CREATE TABLE column breaks every run."""
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "fresh.db")}):
                conn = store.connect()
            for table, expected in (("person_runs", store.PERSON_COLS),
                                    ("repo_runs", store.REPO_COLS)):
                self.assertLessEqual(set(expected), _cols(conn, table), table)


if __name__ == "__main__":
    unittest.main()
