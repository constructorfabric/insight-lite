"""Guards for the data-freshness signal (/health/data + alert.py).

This exists because of a concrete incident: the nightly refresh failed for ten
consecutive nights in July 2026 and every surface stayed green — the portal served
the last good day, /health said "ok", and the traceback sat unread in a log file.
These tests pin the one thing that would have caught it: a stale run reads as stale.
"""
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import store


def _seed(conn, generated_at: str) -> None:
    store.upsert_run(conn, {
        "generated_at": generated_at, "lookback_days": 30, "org": "acme",
        "people": {"alice": {"commits": 1, "features": 1}}, "repos": {},
    })


def _stamp(hours_ago: float) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class LatestRunMetaTest(unittest.TestCase):
    def test_reads_newest_without_the_payload(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                self.assertIsNone(store.latest_run_meta(conn))
                _seed(conn, "2026-07-20T10:00:00Z")
                _seed(conn, "2026-07-27T10:00:00Z")
                meta = store.latest_run_meta(conn)
                self.assertEqual(meta["date"], "2026-07-27")
                self.assertEqual(meta["generated_at"], "2026-07-27T10:00:00Z")
                self.assertNotIn("payload", meta)


class DataFreshnessTest(unittest.TestCase):
    def _freshness(self, tmp, **kw):
        import server
        with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
            return server.data_freshness(**kw)

    def test_recent_run_is_ok(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                _seed(store.connect(), _stamp(2))
            payload, ok = self._freshness(tmp)
            self.assertTrue(ok)
            self.assertFalse(payload["stale"])
            self.assertLess(payload["age_hours"], 3)

    def test_old_run_is_stale(self):
        """The actual incident shape: collection stopped, the last run keeps aging."""
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                _seed(store.connect(), _stamp(24 * 10))
            payload, ok = self._freshness(tmp)
            self.assertFalse(ok)
            self.assertTrue(payload["stale"])
            self.assertIn("240", payload["reason"].replace(".0", ""))

    def test_empty_db_is_stale_not_a_crash(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                store.connect()
            payload, ok = self._freshness(tmp)
            self.assertFalse(ok)
            self.assertEqual(payload["reason"], "no run stored")

    def test_limit_is_honoured(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                _seed(store.connect(), _stamp(30))
            self.assertTrue(self._freshness(tmp, max_age_hours=36)[1])
            self.assertFalse(self._freshness(tmp, max_age_hours=24)[1])


class AlertChannelTest(unittest.TestCase):
    def test_send_without_a_webhook_reports_failure_but_does_not_raise(self):
        import alert
        with patch.object(alert, "WEBHOOK", ""):
            self.assertFalse(alert.send("anything"))

    def test_send_posts_the_message_when_a_webhook_is_configured(self):
        import alert
        seen = {}

        class _Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _urlopen(req, timeout=None):
            seen["url"] = req.full_url
            seen["body"] = req.data.decode()
            return _Resp()

        with patch.object(alert, "WEBHOOK", "https://hooks.example/abc"), \
             patch.object(alert, "LABEL", "prod"), \
             patch.object(alert.urllib.request, "urlopen", _urlopen):
            self.assertTrue(alert.send("data is STALE"))
        self.assertEqual(seen["url"], "https://hooks.example/abc")
        self.assertIn("data is STALE", seen["body"])
        # the message must name the environment, not an opaque container id
        self.assertIn("[insight:prod]", seen["body"])

    def test_check_exits_nonzero_on_stale_data(self):
        import alert
        with patch.object(alert, "WEBHOOK", ""), \
             patch("server.data_freshness",
                   return_value=({"reason": "newest run is 240.0h old (limit 36h)",
                                  "last_run": "2026-07-17"}, False)):
            self.assertEqual(alert.check(), 1)

    def test_check_exits_zero_when_fresh(self):
        import alert
        with patch("server.data_freshness",
                   return_value=({"last_run": "2026-07-27", "age_hours": 2.0}, True)):
            self.assertEqual(alert.check(), 0)


if __name__ == "__main__":
    unittest.main()
