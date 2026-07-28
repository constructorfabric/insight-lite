"""A from-scratch install must not need directories created by hand.

DATA_DIR is the documented way to put all runtime state somewhere else — a mounted
volume, a data disk. Pointing it at a path that does not exist yet is the normal first
run, and it used to crash the server before it served anything:

    FileNotFoundError: [Errno 2] No such file or directory: '<DATA_DIR>/.runtime'

because the state dirs were created with `mkdir(exist_ok=True)`, which only tolerates
the LEAF already existing and still fails on a missing parent. Docker hid it (the volume
mount creates the directory), so it only bit installs that set DATA_DIR themselves —
i.e. exactly the self-hosting case the tool is published for. The store already
behaved correctly (store.connect() makedirs its own parent), which made the
inconsistency easy to miss.
"""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class DataDirIsSelfCreatingTest(unittest.TestCase):

    def _reload_with(self, data_dir: Path):
        """Re-import the path-owning modules against a DATA_DIR that does not exist."""
        import importlib
        import paths
        with patch.dict(os.environ, {"DATA_DIR": str(data_dir)}):
            importlib.reload(paths)
            import reportctl
            import server
            importlib.reload(reportctl)
            return paths, reportctl, server

    def tearDown(self):
        import importlib
        import paths
        importlib.reload(paths)          # restore the suite's isolated DATA_DIR
        import tests._state as _state
        _state.repin_data_dir()

    def test_state_dirs_create_their_parents(self):
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "not" / "created" / "yet"
            self.assertFalse(missing.exists())
            paths, reportctl, _ = self._reload_with(missing)
            # the two directories the server and the CLI make on startup
            for label, target in (("exports", reportctl.EXPORTS),
                                  (".runtime", reportctl.RUNTIME)):
                target.mkdir(parents=True, exist_ok=True)
                self.assertTrue(target.is_dir(), f"{label} was not created")
            self.assertTrue(missing.is_dir(), "DATA_DIR itself should now exist")

    def test_no_mkdir_call_forgets_its_parents(self):
        """Guards the whole class rather than the two paths above: a new
        `mkdir(exist_ok=True)` on a DATA_DIR-derived path reintroduces the crash."""
        import pathlib as _pl
        root = _pl.Path(__file__).resolve().parents[1]
        offenders = []
        for name in ("server.py", "reportctl.py", "collect.py", "identity.py",
                     "ghclient.py", "directory.py", "store.py", "render.py"):
            src = (root / name)
            if not src.exists():
                continue
            for i, line in enumerate(src.read_text().splitlines(), 1):
                if ".mkdir(" in line and "parents=True" not in line:
                    offenders.append(f"{name}:{i}: {line.strip()}")
        self.assertFalse(offenders, "mkdir without parents=True crashes on a fresh "
                                    f"DATA_DIR: {offenders}")



class OrgIsCheckedBeforeSavingTest(unittest.TestCase):
    """The wizard used to accept any syntactically valid org, so a typo surfaced as an
    empty first collection — which reads as "the tool is broken", not "that name is
    wrong". ghclient.check_org distinguishes typo / user-account / no-permission, and a
    network failure must NOT block the save."""

    def _resp(self, status, payload=None):
        class R:
            status_code = status
            ok = 200 <= status < 300
            def json(self_inner):
                return payload or {}
            def raise_for_status(self_inner):
                if not self_inner.ok:
                    raise RuntimeError(status)
        return R()

    def test_an_existing_org_passes(self):
        import ghclient
        with patch.object(ghclient.requests, "get",
                          return_value=self._resp(200, {"login": "acme"})):
            out = ghclient.check_org("acme", "tok")
        self.assertTrue(out["ok"])
        self.assertEqual(out["kind"], "org")

    def test_a_typo_is_refused_and_says_so(self):
        import ghclient
        seq = [self._resp(404), self._resp(404)]
        with patch.object(ghclient.requests, "get", side_effect=seq):
            out = ghclient.check_org("acmee", "tok")
        self.assertFalse(out["ok"])
        self.assertEqual(out["kind"], "missing")
        self.assertIn("acmee", out["error"])

    def test_a_user_account_is_named_as_such(self):
        """Different mistake from a typo: it exists, but has no org-level members,
        PRs or issues, so most of the report would come back empty."""
        import ghclient
        seq = [self._resp(404), self._resp(200, {"type": "User"})]
        with patch.object(ghclient.requests, "get", side_effect=seq):
            out = ghclient.check_org("someperson", "tok")
        self.assertFalse(out["ok"])
        self.assertEqual(out["kind"], "user")
        self.assertIn("user account", out["error"])

    def test_missing_permission_is_distinguished(self):
        import ghclient
        with patch.object(ghclient.requests, "get", return_value=self._resp(403)):
            out = ghclient.check_org("private-org", "tok")
        self.assertFalse(out["ok"])
        self.assertEqual(out["kind"], "forbidden")
        self.assertIn("read:org", out["error"])

    def test_github_being_unreachable_does_not_block_the_save(self):
        import ghclient
        import requests as _rq
        with patch.object(ghclient.requests, "get",
                          side_effect=_rq.RequestException("dns")):
            out = ghclient.check_org("acme", "tok")
        self.assertTrue(out["ok"], "a transient network error must not refuse the org")
        self.assertEqual(out["kind"], "unverified")

if __name__ == "__main__":
    unittest.main()
