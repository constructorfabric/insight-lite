"""Makes `tests` a package so that `python -m unittest discover -s tests` — the
command .github/workflows/tests.yml runs — imports this file BEFORE any test module,
and therefore before any module under test binds its state paths at import time.

That import is the whole point of the file. State isolation and the seeded fixture
store used to live in conftest.py, which only pytest loads, so under the CI runner
tests read and wrote the checkout's own history/report.db with no canary watching.
See tests/_state.py.
"""
from . import _state as _state  # noqa: F401 — imported for its import-time effects
