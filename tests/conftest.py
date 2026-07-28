"""Pytest half of the suite's state isolation: the fixtures and the leak canary.

The redirection itself — DATA_DIR, CLONE_DIR and the seeded REPORT_DB — lives in
tests/_state.py and runs at ITS import time, so that `unittest discover` (what CI
runs) gets it too via tests/__init__.py. This file only adds what needs pytest:
the autouse re-pin and the session-wide backstop. See tests/_state.py for the
history behind both, including the 2026-07-28 people.yaml / history-backup leak.
"""
from __future__ import annotations

import pytest

from . import _state

STATE_DIR = _state.STATE_DIR
checkout_state_fingerprint = _state.checkout_state_fingerprint


@pytest.fixture(autouse=True)
def _data_dir_pinned():
    """Re-pin paths.DATA_DIR after each test — see _state.repin_data_dir."""
    yield
    _state.repin_data_dir()


@pytest.fixture(scope="session")
def checkout_state_diff():
    """{relative path: (before, after)} for checkout state touched by this run —
    asserted empty by tests/test_state_isolation.py."""
    return _state.changed_since_start


@pytest.fixture(scope="session", autouse=True)
def _checkout_untouched():
    """Backstop for test_state_isolation: that test only sees leaks from modules
    collected before it, this sees the whole session."""
    yield
    changed = _state.changed_since_start()
    assert not changed, (
        "this run wrote into the checkout's runtime state — the 2026-07-28 "
        f"people.yaml / history-backup leak is back: {changed}")

# No pytest_sessionfinish for the temp dir: _state registers its own atexit cleanup, so
# both runners drop it. A pytest-only hook is what left unittest leaking one per run.
