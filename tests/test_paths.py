"""paths.py — DATA_DIR resolution and the REPORT_DB > DATA_DIR precedence.

DATA_DIR centralises every runtime-state path (report.db, people.yaml, caches,
clones, history/, exports/...) so a Docker image swap never loses data (state
lives on a volume mounted at DATA_DIR, never baked into the image). Local dev
and the existing test suite rely on the default (DATA_DIR unset -> ".", i.e.
the repo root) resolving exactly like before this change.
"""
import importlib
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import paths


class DataDirDefaultTest(unittest.TestCase):
    """DATA_DIR is computed once at import time from the DATA_DIR env var,
    defaulting to ".", i.e. the current working directory (the repo root for
    every local/test/Docker invocation)."""

    def tearDown(self):
        importlib.reload(paths)          # restore the real default for later tests

    def test_default_is_cwd_when_unset(self):
        env = os.environ.copy()
        env.pop("DATA_DIR", None)
        with patch.dict(os.environ, env, clear=True):
            importlib.reload(paths)
            self.assertEqual(paths.DATA_DIR, Path(".").resolve())

    def test_env_override_is_honoured(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"DATA_DIR": tmp}):
                importlib.reload(paths)
                self.assertEqual(paths.DATA_DIR, Path(tmp).resolve())

    def test_data_path_joins_under_data_dir(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"DATA_DIR": tmp}):
                importlib.reload(paths)
                self.assertEqual(paths.data_path("report.db"),
                                  Path(tmp).resolve() / "report.db")
                self.assertEqual(paths.data_path("history", "report.db"),
                                  Path(tmp).resolve() / "history" / "report.db")


class DbPathPrecedenceTest(unittest.TestCase):
    """store.db_path(): the REPORT_DB env override always wins (existing tests
    rely on this for full isolation); absent that, it resolves under DATA_DIR
    at history/report.db — the same relative layout as before this change, so
    a local checkout with DATA_DIR unset keeps reading its existing DB file."""

    def test_report_db_env_wins_over_data_dir(self):
        import store
        with TemporaryDirectory() as tmp:
            explicit = str(Path(tmp) / "explicit.db")
            with patch.dict(os.environ, {"REPORT_DB": explicit,
                                          "DATA_DIR": str(Path(tmp) / "elsewhere")}):
                self.assertEqual(store.db_path(), explicit)

    def test_default_db_path_resolves_under_data_dir(self):
        import store
        with TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env.pop("REPORT_DB", None)
            with patch.dict(os.environ, env, clear=True), \
                 patch.object(paths, "DATA_DIR", Path(tmp)):
                self.assertEqual(store.db_path(), str(Path(tmp) / "history" / "report.db"))

    def test_default_db_path_matches_repo_root_when_data_dir_unset(self):
        """Local-dev sanity check: with DATA_DIR unset (its documented default),
        db_path() lands exactly where it did before DATA_DIR existed."""
        import store
        env = os.environ.copy()
        env.pop("REPORT_DB", None)
        env.pop("DATA_DIR", None)
        try:
            with patch.dict(os.environ, env, clear=True):
                importlib.reload(paths)
                importlib.reload(store)
                self.assertEqual(store.db_path(),
                                  os.path.join(store.ROOT, "history", "report.db"))
        finally:
            # reload AFTER the env patch is lifted: reloading while DATA_DIR is
            # still stripped re-pins paths.DATA_DIR to the checkout for the rest
            # of the session, and every module imported after this point picks
            # the checkout up as its write target (see tests/conftest.py)
            importlib.reload(paths)
            importlib.reload(store)


if __name__ == "__main__":
    unittest.main()
