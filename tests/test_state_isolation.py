"""The suite must not be able to write into the checkout's runtime state.

Guards the 2026-07-28 incident described in tests/conftest.py: every dated backup
under history/people/ had been overwritten with test fixture data, so a real
roster had to be recovered from a DB snapshot. Three halves:

- the redirection is actually in effect for the constants that bind at import
  time (this fails loudly if conftest.py is removed, or if a module starts
  resolving state some other way);
- no module resolves a people.yaml / config.local.yaml path at all — the files and
  every reader and writer of them were removed the same day, and a re-introduced
  constant is how the whole class of bug would come back;
- nothing under the checkout changed while this run was in progress.

The first two halves are a unittest.TestCase, deliberately: `unittest discover`
collects only TestCase subclasses, so as plain pytest functions they did not run at
all under the runner .github/workflows/tests.yml uses — the guard against writing into
the checkout was missing from exactly the run that gates merges. They need no fixture,
so there is nothing keeping them out of a TestCase. The third stays a pytest function
because it needs the session-scoped fixture.

If isolation is not installed (someone runs `unittest discover -s tests` without
`-t .`, so tests/__init__.py is never imported), the TestCase below fails loudly
instead of letting the run quietly write real state.
"""
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _outside_checkout(p) -> bool:
    resolved = Path(p).resolve()
    return resolved != REPO_ROOT and REPO_ROOT not in resolved.parents


class StateIsolationTest(unittest.TestCase):
    """Runs under BOTH pytest and unittest — see the module docstring."""

    def test_state_paths_resolve_outside_the_checkout(self):
        import collect
        import directory
        import ghclient
        import identity
        import paths
        import reportctl
        import server
        import store

        targets = {
            "paths.DATA_DIR": paths.DATA_DIR,
            "store.db_path()": store.db_path(),
            "ghclient.CACHE_DIR": ghclient.CACHE_DIR,
            "collect.CLONE_ROOT": collect.CLONE_ROOT,
            "identity.CLONE_ROOT": identity.CLONE_ROOT,
            "server.EXPORTS": server.EXPORTS,
            "server.RUNTIME": server.RUNTIME,
            "reportctl.EXPORTS": reportctl.EXPORTS,
            "reportctl.RUNTIME": reportctl.RUNTIME,
        }
        leaking = {name: str(p) for name, p in targets.items() if not _outside_checkout(p)}
        self.assertFalse(leaking, f"these would write into the checkout: {leaking}")

    def test_no_module_resolves_a_yaml_state_path(self):
        """server.PEOPLE_YAML and directory.DIR used to be listed above. Both are gone
        with the files: the roster and the config overlay live only in the override
        table, and the seed that read those files back had imported a test fixture into
        the prod table. A new module-level constant pointing at either name is the
        regression."""
        import collect
        import configstore
        import directory
        import server
        import store

        dead = {}
        for mod in (server, directory, configstore, store, collect):
            for name in dir(mod):
                if name.startswith("__"):
                    continue
                val = getattr(mod, name)
                if not isinstance(val, (str, Path)):
                    continue
                # a resolved path, not prose: the editor templates are module-level
                # strings too, and they still describe the removed file in their comments
                text = str(val)
                if len(text) < 500 and text.endswith(("people.yaml", "config.local.yaml")):
                    dead[f"{mod.__name__}.{name}"] = text
        self.assertFalse(dead, f"these resolve a removed YAML state path: {dead}")


def test_the_run_has_not_touched_the_checkout(checkout_state_diff):
    assert not checkout_state_diff(), (
        f"checkout runtime state changed during this run: {checkout_state_diff()}")
