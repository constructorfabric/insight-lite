import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import collect
import store


class ParseCiRunsTest(unittest.TestCase):
    def test_fields_and_duration(self):
        runs = [{
            "id": 555, "name": "CI", "event": "pull_request", "head_branch": "feature",
            "status": "completed", "conclusion": "success",
            "created_at": "2026-07-10T10:00:00Z", "run_started_at": "2026-07-10T10:00:05Z",
            "updated_at": "2026-07-10T10:03:05Z", "head_sha": "abc",
            "actor": {"login": "alice"},
        }]
        row = collect._parse_ci_runs("o/r", runs)[0]
        self.assertEqual(row["run_id"], 555)
        self.assertEqual(row["workflow"], "CI")
        self.assertEqual(row["conclusion"], "success")
        self.assertEqual(row["duration_s"], 180)     # 3 minutes
        self.assertEqual(row["actor"], "alice")

    def test_incomplete_run_has_no_duration(self):
        runs = [{"id": 1, "name": "CI", "status": "in_progress",
                 "run_started_at": "2026-07-10T10:00:00Z", "created_at": "2026-07-10T10:00:00Z"}]
        self.assertIsNone(collect._parse_ci_runs("o/r", runs)[0]["duration_s"])

    def test_missing_actor_and_times(self):
        row = collect._parse_ci_runs("o/r", [{"id": 2, "name": "x", "status": "completed"}])[0]
        self.assertIsNone(row["duration_s"])
        self.assertIsNone(row["actor"])


class WriteCiRunsTest(unittest.TestCase):
    def _row(self, run_id, created, conclusion="success"):
        return {"repo": "o/r", "run_id": run_id, "workflow": "CI", "event": "push",
                "branch": "main", "status": "completed", "conclusion": conclusion,
                "created_at": created, "run_started_at": created, "updated_at": created,
                "duration_s": 10, "head_sha": "s", "actor": "a"}

    def test_window_replace_preserves_older_runs(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                # an older wide collection wrote a run from June
                store.write_ci_runs(conn, [self._row(1, "2026-06-01T00:00:00Z")])
                # a later window-bounded run (since July) must not wipe June
                store.write_ci_runs(conn, [self._row(2, "2026-07-05T00:00:00Z")],
                                    since="2026-07-01T00:00:00Z")
                ids = {r["run_id"] for r in conn.execute("SELECT run_id FROM ci_run")}
                self.assertEqual(ids, {1, 2})
                # re-running the July window replaces only in-window rows
                store.write_ci_runs(conn, [self._row(3, "2026-07-06T00:00:00Z")],
                                    since="2026-07-01T00:00:00Z")
                ids = {r["run_id"] for r in conn.execute("SELECT run_id FROM ci_run")}
                self.assertEqual(ids, {1, 3})     # June kept, run 2 gone, run 3 in


if __name__ == "__main__":
    unittest.main()
