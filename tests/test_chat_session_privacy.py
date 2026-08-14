"""A chat transcript is confidential to the person who held the conversation.

The /chat-log viewer (server.py /api/chat-sessions and /api/chat-session) used to return
ANY session's full transcript and tool calls filtered only by session_id — so any
portal-authenticated employee could enumerate everyone's sessions and read another
person's questions and the data the tools returned about them. store.chat_sessions and
chat_session_detail now take the resolved viewer and scope to their own rows.
"""
import contextlib
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

# record_chat_message stamps ts = now, so the list window must span it.
SINCE, UNTIL = "2020-01-01", "2035-01-01"


@contextlib.contextmanager
def _store():
    with TemporaryDirectory() as tmp:
        with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
            import store
            conn = store.connect()
            yield store, conn
            conn.close()


def _make_admin(conn, login):
    # org admins are read from the latest membership snapshot (role='admin').
    conn.execute("INSERT INTO membership_snapshot (date, org, login, role) "
                 "VALUES ('2026-06-30', 'o', ?, 'admin')", (login,))
    conn.commit()


def _seed(store, conn):
    # alice (resolved to a person login) and bob (login) each hold a session; carol has
    # only a raw proxy identity, no person row.
    a = store.record_chat_message(conn, "sess-alice", "alice", "alice@co", "user",
                                  "what is my 30d friction?")
    store.record_chat_tool_calls(conn, "sess-alice", "alice", "alice@co", a,
                                 [{"name": "developer_score", "args": {"login": "alice"},
                                   "result": "secret-ish", "ok": True}])
    store.record_chat_message(conn, "sess-bob", "bob", "bob@co", "user",
                              "bob's private question")
    store.record_chat_message(conn, "sess-carol", None, "carol@co", "user",
                              "carol has no person row")


class ChatSessionScopeTest(unittest.TestCase):
    def test_the_list_shows_only_your_own_sessions(self):
        with _store() as (store, conn):
            _seed(store, conn)
            mine = store.chat_sessions(conn, SINCE, UNTIL, "alice", "alice@co")
            ids = {r["session_id"] for r in mine}
            self.assertEqual(ids, {"sess-alice"})
            self.assertNotIn("sess-bob", ids)

    def test_you_cannot_open_someone_elses_session_by_id(self):
        with _store() as (store, conn):
            _seed(store, conn)
            # alice asks for bob's session id directly
            got = store.chat_session_detail(conn, "sess-bob", "alice", "alice@co")
            self.assertEqual(got["messages"], [],
                             "another person's transcript must not come back")
            self.assertEqual(got["tools"], {})

    def test_you_can_open_your_own(self):
        with _store() as (store, conn):
            _seed(store, conn)
            got = store.chat_session_detail(conn, "sess-alice", "alice", "alice@co")
            self.assertEqual(len(got["messages"]), 1)
            self.assertTrue(got["tools"], "own tool calls are returned")

    def test_identity_only_viewer_sees_their_own(self):
        """carol has no person login — matched on her raw proxy identity, not on nothing."""
        with _store() as (store, conn):
            _seed(store, conn)
            mine = store.chat_sessions(conn, SINCE, UNTIL, None, "carol@co")
            self.assertEqual({r["session_id"] for r in mine}, {"sess-carol"})
            other = store.chat_session_detail(conn, "sess-alice", None, "carol@co")
            self.assertEqual(other["messages"], [])

    def test_an_unresolved_anon_viewer_does_not_get_everything(self):
        """login None + ident 'anon'/'' must match only same-shaped rows, never the world."""
        with _store() as (store, conn):
            _seed(store, conn)
            self.assertEqual(store.chat_sessions(conn, SINCE, UNTIL, None, "anon"), [])
            self.assertEqual(
                store.chat_session_detail(conn, "sess-alice", None, "anon")["messages"], [])


class ChatAdminTest(unittest.TestCase):
    """Org admins (role='admin' in the latest membership snapshot) read everyone's
    transcripts; everyone else stays scoped to their own."""

    def test_an_admin_sees_every_session(self):
        with _store() as (store, conn):
            _seed(store, conn)
            _make_admin(conn, "alice")
            self.assertTrue(store.is_chat_log_admin(conn, "alice"))
            ids = {r["session_id"] for r in store.chat_sessions(
                conn, SINCE, UNTIL, "alice", "alice@co", all_sessions=True)}
            self.assertEqual(ids, {"sess-alice", "sess-bob", "sess-carol"})

    def test_an_admin_can_open_anyone_elses_session(self):
        with _store() as (store, conn):
            _seed(store, conn)
            _make_admin(conn, "alice")
            got = store.chat_session_detail(conn, "sess-bob", "alice", "alice@co",
                                            all_sessions=True)
            self.assertEqual(len(got["messages"]), 1, "bob's transcript is visible to an admin")

    def test_a_non_admin_is_not_an_admin(self):
        with _store() as (store, conn):
            _seed(store, conn)
            _make_admin(conn, "alice")
            self.assertFalse(store.is_chat_log_admin(conn, "bob"))
            self.assertFalse(store.is_chat_log_admin(conn, None), "no login is never admin")

    def test_only_the_latest_snapshot_defines_admins(self):
        """Someone who was an admin in an OLD snapshot but not the newest is not one now."""
        with _store() as (store, conn):
            _seed(store, conn)
            conn.execute("INSERT INTO membership_snapshot (date, org, login, role) "
                         "VALUES ('2026-05-01', 'o', 'bob', 'admin')")
            _make_admin(conn, "alice")   # newest snapshot 2026-06-30, admins={alice}
            self.assertTrue(store.is_chat_log_admin(conn, "alice"))
            self.assertFalse(store.is_chat_log_admin(conn, "bob"))


if __name__ == "__main__":
    unittest.main()
