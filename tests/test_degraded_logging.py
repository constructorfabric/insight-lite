"""The remaining best-effort paths in server.py must not degrade in silence.

Companion to test_score_availability.py. That one covers the Developer-score panel;
this one covers the swallowed-exception paths the same audit turned up, and above all
the worst of them: _report_model()'s fallback to the last-good model.

Why that one deserves its own guard. Every other degraded path here costs one request
— a missing delta caption, an unwritten analytics row. This one is process-wide and
self-perpetuating: nothing advances the cached version past a failing rebuild, so
after the first failure EVERY request re-runs the same failing build and serves the
same frozen model, on every endpoint, answering 200. That is the July 2026 outage
exactly (healthy-looking portal, numbers that quietly stopped moving), which is why it
is not enough for it to be merely logged: /health/data has to fail so a monitor can
page on it, and it has to stop failing once a rebuild succeeds.
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

import server


class StaleModelFallbackTest(unittest.TestCase):
    """server._report_model() serving a stale model is recorded, not swallowed."""

    def setUp(self):
        # These are module globals in a threaded server; each test gets a clean slate
        # and puts them back, so ordering can't leak a "stale" flag between tests.
        self._render = dict(server._RENDER)
        self._stale = dict(server._STALE_MODEL)
        server._RENDER.update(version=None, model=None, ctx=None, html=None)
        server._STALE_MODEL.update(since=None, version=None, error=None,
                                   logged_version=server._UNSET_VERSION)

    def tearDown(self):
        server._RENDER.clear()
        server._RENDER.update(self._render)
        server._STALE_MODEL.clear()
        server._STALE_MODEL.update(self._stale)

    @staticmethod
    def _seed_last_good(model, version="v1"):
        server._RENDER.update(version=version, model=model, ctx=None, html=None)

    def test_a_failing_rebuild_serves_the_last_good_model_and_says_so(self):
        self._seed_last_good({"marker": "last-good"})
        err = io.StringIO()
        with patch("render.load_data", side_effect=RuntimeError("aggregate blew up")), \
             redirect_stderr(err):
            got = server._report_model("v2")
        # the page still renders — that intent was never in question
        self.assertEqual(got, {"marker": "last-good"})
        log = err.getvalue()
        self.assertIn("degraded", log)
        self.assertIn("report model rebuild", log)
        # the traceback, not just the sentence, so it is actionable
        self.assertIn("RuntimeError", log)
        self.assertIn("aggregate blew up", log)
        self.assertIn("Traceback", log)

    def test_the_stale_serve_is_visible_to_health_data(self):
        self._seed_last_good({"marker": "last-good"})
        self.assertIsNone(server.stale_model_state(), "clean state must report nothing")
        with patch("render.load_data", side_effect=RuntimeError("boom")), \
             redirect_stderr(io.StringIO()):
            server._report_model("v2")
        state = server.stale_model_state()
        self.assertIsNotNone(state)
        self.assertTrue(state["stale_since"])
        self.assertIn("boom", state["error"])
        self.assertIn("RuntimeError", state["error"])

    @staticmethod
    def _fresh_data():
        """Collector doing its job: a run landed two hours ago."""
        return patch("server.data_freshness",
                     return_value=({"ok": True, "stale": False,
                                    "last_run": "2026-07-28", "age_hours": 2.0}, True))

    def test_health_data_answers_503_while_the_model_is_frozen(self):
        """The endpoint's whole promise is "the data is still being refreshed". A
        frozen model falsifies it even when the collector is landing runs on time, so
        fresh runs + frozen model must NOT answer 200."""
        self._seed_last_good({"marker": "last-good"})
        with patch("render.load_data", side_effect=RuntimeError("boom")), \
             redirect_stderr(io.StringIO()):
            server._report_model("v2")

        with self._fresh_data():
            payload, ok = server.health_data_payload()

        self.assertFalse(ok, "fresh runs must not excuse a model that stopped building")
        self.assertFalse(payload["ok"])
        self.assertIn("report_model", payload)
        self.assertIn("boom", payload["report_model"]["error"])
        # the reason has to say which of the two broke, or a pager is useless
        self.assertIn("has not rebuilt", payload["reason"])

    def test_health_data_is_200_when_only_the_collector_is_healthy_and_model_is_fine(self):
        """The guard must not turn every /health/data into a 503."""
        with self._fresh_data():
            payload, ok = server.health_data_payload()
        self.assertTrue(ok)
        self.assertNotIn("report_model", payload)

    def test_health_data_keeps_the_stale_data_reason_when_both_are_broken(self):
        """Losing the collector's reason to the model's would hide half the outage."""
        self._seed_last_good({"marker": "last-good"})
        with patch("render.load_data", side_effect=RuntimeError("boom")), \
             redirect_stderr(io.StringIO()):
            server._report_model("v2")

        stale_data = ({"ok": False, "stale": True, "last_run": "2026-07-17",
                       "reason": "newest run is 240.0h old (limit 36h)"}, False)
        with patch("server.data_freshness", return_value=stale_data):
            payload, ok = server.health_data_payload()

        self.assertFalse(ok)
        self.assertIn("has not rebuilt", payload["reason"])
        self.assertIn("240.0h old", payload["reason"])

    def test_a_persistent_failure_logs_once_per_version_not_once_per_request(self):
        """Dedupe is load-bearing: without it a broken rebuild prints a traceback on
        every hit, and the flood is what makes people stop reading the log."""
        self._seed_last_good({"marker": "last-good"})
        err = io.StringIO()
        with patch("render.load_data", side_effect=RuntimeError("boom")), \
             redirect_stderr(err):
            for _ in range(5):
                server._report_model("v2")
        self.assertEqual(err.getvalue().count("report model rebuild"), 1)

    def test_a_new_version_that_still_fails_logs_again(self):
        """A fresh collect landing on a still-broken renderer is new information."""
        self._seed_last_good({"marker": "last-good"})
        err = io.StringIO()
        with patch("render.load_data", side_effect=RuntimeError("boom")), \
             redirect_stderr(err):
            server._report_model("v2")
            server._report_model("v2")
            server._report_model("v3")
        self.assertEqual(err.getvalue().count("report model rebuild"), 2)

    def test_recovery_clears_the_record_and_is_announced(self):
        self._seed_last_good({"marker": "last-good"})
        err = io.StringIO()
        with patch("render.load_data", side_effect=RuntimeError("boom")), \
             redirect_stderr(err):
            server._report_model("v2")
        self.assertIsNotNone(server.stale_model_state())

        with patch("render.load_data", return_value={"raw": 1}), \
             patch("render.build_model", return_value={"marker": "rebuilt"}), \
             redirect_stderr(err):
            got = server._report_model("v3")

        self.assertEqual(got, {"marker": "rebuilt"})
        self.assertIsNone(server.stale_model_state(), "must not stay stale forever")
        self.assertIn("recovered", err.getvalue())

    def test_no_last_good_model_still_raises(self):
        """With nothing to fall back on the caller turns this into a visible error
        (a 500, or SystemExit's "no data yet" on a fresh install), so it needs no
        degradation record — and must not invent one."""
        err = io.StringIO()
        with patch("render.load_data", side_effect=SystemExit("no data yet")), \
             redirect_stderr(err):
            with self.assertRaises(SystemExit):
                server._report_model("v1")
        self.assertIsNone(server.stale_model_state())
        self.assertNotIn("degraded", err.getvalue())


class QuietHelperTest(unittest.TestCase):
    """The portal/setup helpers that swallow a failure now name it.

    Each of these falls back to a value that is ALSO a legitimate state — "nothing
    collected yet", "no token configured", "no tools", "no org set" — so the fallback
    alone tells an operator nothing. store.connect() creates the DB lazily, so none of
    these fire on a fresh install; reaching them means something is actually wrong.
    """

    def _assert_logs(self, fn, needle, *, expect=None):
        err = io.StringIO()
        with redirect_stderr(err):
            got = fn()
        if expect is not None:
            self.assertEqual(got, expect)
        self.assertIn("degraded", err.getvalue())
        self.assertIn(needle, err.getvalue())
        return got

    def test_store_state_reports_an_unreadable_db(self):
        with patch("store.connect", side_effect=OSError("disk gone")):
            info = self._assert_logs(server.store_state, "portal store state")
        # the misleading fallback is preserved (the portal must render) …
        self.assertFalse(info["present"])

    def test_token_status_reports_a_failed_secret_read(self):
        with patch("store.connect", side_effect=OSError("locked")), \
             patch.dict(os.environ, {"GH_TOKEN": "", "GITHUB_TOKEN": ""}, clear=False):
            self._assert_logs(server.token_status, "token source", expect="none")

    def test_data_present_reports_a_failed_check(self):
        with patch("store.connect", side_effect=OSError("locked")):
            self._assert_logs(server.data_present, "data-present check", expect=False)

    def test_mcp_tool_catalog_reports_a_broken_import(self):
        # A None entry in sys.modules makes `import tooldefs` raise ImportError —
        # the real failure mode, without monkeypatching __import__ itself.
        with patch.dict(sys.modules, {"tooldefs": None}):
            self._assert_logs(server._mcp_tools, "MCP tool catalog", expect=[])

    def test_setup_wizard_reports_a_failed_config_load(self):
        """Loudest of the helpers on purpose: the wizard's fields fall back to EMPTY,
        so a failed load shows a configured org as unconfigured — and saving the form
        as presented would write those blanks back over it."""
        with patch("ghclient.load_config", side_effect=OSError("config unreadable")):
            self._assert_logs(server.setup_html, "setup wizard config load")


if __name__ == "__main__":
    unittest.main()
