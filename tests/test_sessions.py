"""
tests/test_sessions.py

Tests for sessions.py:
- Session dataclass fields
- SessionRegistry add / get / remove / count
- get_by_client_id (authenticated vs unauthenticated)
- all_sessions snapshot safety
- Overwrite behaviour on duplicate session_id
"""
import time
import unittest

from sessions import Session, SessionRegistry


class _FakeWS:
    """Minimal stand-in for a WebSocket connection object."""
    pass


def _make_session(
    session_id: str = "s1",
    client_id: str = None,
    authenticated: bool = False,
) -> Session:
    return Session(
        session_id=session_id,
        websocket=_FakeWS(),
        remote_address="127.0.0.1:9000",
        nonce="testnonce",
        created_at=time.monotonic(),
        client_id=client_id,
        authenticated=authenticated,
    )


class TestSessionDefaults(unittest.TestCase):

    def test_default_client_id_is_none(self):
        s = _make_session()
        self.assertIsNone(s.client_id)

    def test_default_authenticated_is_false(self):
        s = _make_session()
        self.assertFalse(s.authenticated)

    def test_default_message_counters_zero(self):
        s = _make_session()
        self.assertEqual(s.messages_received, 0)
        self.assertEqual(s.messages_sent, 0)

    def test_counter_mutation(self):
        s = _make_session()
        s.messages_received += 5
        s.messages_sent += 3
        self.assertEqual(s.messages_received, 5)
        self.assertEqual(s.messages_sent, 3)


class TestSessionRegistryBasic(unittest.TestCase):

    def setUp(self):
        self.reg = SessionRegistry()

    def test_initial_count_zero(self):
        self.assertEqual(self.reg.count(), 0)

    def test_add_and_get(self):
        s = _make_session("s1")
        self.reg.add(s)
        found = self.reg.get("s1")
        self.assertIs(found, s)

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.reg.get("ghost"))

    def test_count_increments_on_add(self):
        self.reg.add(_make_session("s1"))
        self.reg.add(_make_session("s2"))
        self.assertEqual(self.reg.count(), 2)

    def test_remove_returns_session(self):
        s = _make_session("s1")
        self.reg.add(s)
        removed = self.reg.remove("s1")
        self.assertIs(removed, s)

    def test_remove_decrements_count(self):
        self.reg.add(_make_session("s1"))
        self.reg.remove("s1")
        self.assertEqual(self.reg.count(), 0)

    def test_get_after_remove_returns_none(self):
        self.reg.add(_make_session("s1"))
        self.reg.remove("s1")
        self.assertIsNone(self.reg.get("s1"))

    def test_remove_nonexistent_returns_none(self):
        self.assertIsNone(self.reg.remove("ghost"))

    def test_remove_nonexistent_does_not_raise(self):
        self.reg.remove("ghost")  # must not raise


class TestSessionRegistryDuplicateId(unittest.TestCase):

    def test_add_same_id_overwrites(self):
        reg = SessionRegistry()
        s1 = _make_session("s1")
        s2 = _make_session("s1")
        reg.add(s1)
        reg.add(s2)
        self.assertEqual(reg.count(), 1)
        self.assertIs(reg.get("s1"), s2)


class TestSessionRegistryGetByClientId(unittest.TestCase):

    def setUp(self):
        self.reg = SessionRegistry()

    def test_authenticated_session_found(self):
        s = _make_session("s1", client_id="c1", authenticated=True)
        self.reg.add(s)
        found = self.reg.get_by_client_id("c1")
        self.assertIs(found, s)

    def test_unauthenticated_session_not_found(self):
        s = _make_session("s1", client_id="c1", authenticated=False)
        self.reg.add(s)
        self.assertIsNone(self.reg.get_by_client_id("c1"))

    def test_session_without_client_id_not_found(self):
        s = _make_session("s1", client_id=None, authenticated=False)
        self.reg.add(s)
        self.assertIsNone(self.reg.get_by_client_id("c1"))

    def test_missing_client_id_returns_none(self):
        self.assertIsNone(self.reg.get_by_client_id("nobody"))

    def test_only_matching_client_id_returned(self):
        s1 = _make_session("s1", client_id="c1", authenticated=True)
        s2 = _make_session("s2", client_id="c2", authenticated=True)
        self.reg.add(s1)
        self.reg.add(s2)
        self.assertIs(self.reg.get_by_client_id("c1"), s1)
        self.assertIs(self.reg.get_by_client_id("c2"), s2)

    def test_returns_none_after_session_removed(self):
        s = _make_session("s1", client_id="c1", authenticated=True)
        self.reg.add(s)
        self.reg.remove("s1")
        self.assertIsNone(self.reg.get_by_client_id("c1"))


class TestSessionRegistrySnapshot(unittest.TestCase):

    def test_all_sessions_empty(self):
        reg = SessionRegistry()
        self.assertEqual(reg.all_sessions(), [])

    def test_all_sessions_contains_all(self):
        reg = SessionRegistry()
        s1 = _make_session("s1")
        s2 = _make_session("s2")
        reg.add(s1)
        reg.add(s2)
        snap = reg.all_sessions()
        self.assertEqual(len(snap), 2)
        self.assertIn(s1, snap)
        self.assertIn(s2, snap)

    def test_all_sessions_is_snapshot(self):
        """Adding after snapshot must not affect the snapshot."""
        reg = SessionRegistry()
        reg.add(_make_session("s1"))
        snap = reg.all_sessions()
        reg.add(_make_session("s2"))
        self.assertEqual(len(snap), 1)


if __name__ == "__main__":
    unittest.main()
