"""
tests/test_clients.py

Tests for clients.py:
- ClientRecord creation and mutation (touch, is_timed_out)
- ClientRegistry CRUD operations
- Dual-index consistency (session + client_id)
- Duplicate client_id replacement
- Capacity (is_full / count)
- Edge cases (remove nonexistent, get nonexistent)
- all_records snapshot safety
"""
import time
import unittest

from clients import ClientRecord, ClientRegistry, ClientState


# ---------------------------------------------------------------------------
# ClientRecord
# ---------------------------------------------------------------------------

class TestClientRecord(unittest.TestCase):

    def _make(self, **kw) -> ClientRecord:
        now = time.monotonic()
        defaults = dict(
            client_id="client_a",
            session_id="session_1",
            remote_address="127.0.0.1:9000",
            connected_at=now,
            last_seen=now,
        )
        defaults.update(kw)
        return ClientRecord(**defaults)

    def test_initial_state_is_connected(self):
        record = self._make()
        self.assertEqual(record.state, ClientState.CONNECTED)

    def test_touch_updates_last_seen(self):
        record = self._make()
        before = record.last_seen
        time.sleep(0.01)
        record.touch()
        self.assertGreater(record.last_seen, before)

    def test_not_timed_out_immediately(self):
        record = self._make()
        self.assertFalse(record.is_timed_out(timeout=60))

    def test_timed_out_when_last_seen_old(self):
        now = time.monotonic()
        record = self._make(last_seen=now - 100)
        self.assertTrue(record.is_timed_out(timeout=60))

    def test_not_timed_out_well_within_timeout(self):
        # last_seen 59 seconds ago with a 60-second timeout — clearly still live.
        now = time.monotonic()
        record = self._make(last_seen=now - 59)
        self.assertFalse(record.is_timed_out(timeout=60))


# ---------------------------------------------------------------------------
# ClientRegistry — basic operations
# ---------------------------------------------------------------------------

class TestClientRegistryBasic(unittest.TestCase):

    def setUp(self):
        self.reg = ClientRegistry(max_clients=10)

    def test_initial_count_zero(self):
        self.assertEqual(self.reg.count(), 0)

    def test_register_returns_record(self):
        record = self.reg.register("c1", "s1", "1.2.3.4:100")
        self.assertIsInstance(record, ClientRecord)
        self.assertEqual(record.client_id, "c1")
        self.assertEqual(record.session_id, "s1")
        self.assertEqual(record.remote_address, "1.2.3.4:100")

    def test_count_after_register(self):
        self.reg.register("c1", "s1", "x")
        self.assertEqual(self.reg.count(), 1)
        self.reg.register("c2", "s2", "y")
        self.assertEqual(self.reg.count(), 2)

    def test_get_by_session(self):
        self.reg.register("c1", "s1", "x")
        record = self.reg.get_by_session("s1")
        self.assertIsNotNone(record)
        self.assertEqual(record.client_id, "c1")

    def test_get_by_session_missing_returns_none(self):
        self.assertIsNone(self.reg.get_by_session("nonexistent"))

    def test_get_by_client_id(self):
        self.reg.register("c1", "s1", "x")
        record = self.reg.get_by_client_id("c1")
        self.assertIsNotNone(record)
        self.assertEqual(record.session_id, "s1")

    def test_get_by_client_id_missing_returns_none(self):
        self.assertIsNone(self.reg.get_by_client_id("nobody"))

    def test_remove_returns_record(self):
        self.reg.register("c1", "s1", "x")
        removed = self.reg.remove("s1")
        self.assertIsNotNone(removed)
        self.assertEqual(removed.client_id, "c1")

    def test_count_decreases_after_remove(self):
        self.reg.register("c1", "s1", "x")
        self.reg.remove("s1")
        self.assertEqual(self.reg.count(), 0)

    def test_remove_clears_both_indexes(self):
        self.reg.register("c1", "s1", "x")
        self.reg.remove("s1")
        self.assertIsNone(self.reg.get_by_session("s1"))
        self.assertIsNone(self.reg.get_by_client_id("c1"))

    def test_remove_nonexistent_returns_none(self):
        result = self.reg.remove("ghost")
        self.assertIsNone(result)

    def test_remove_nonexistent_does_not_raise(self):
        self.reg.remove("ghost")  # must not raise


# ---------------------------------------------------------------------------
# ClientRegistry — duplicate client_id
# ---------------------------------------------------------------------------

class TestClientRegistryDuplicate(unittest.TestCase):

    def setUp(self):
        self.reg = ClientRegistry(max_clients=10)

    def test_second_register_same_client_id_replaces_session(self):
        self.reg.register("c1", "old_session", "x")
        self.reg.register("c1", "new_session", "y")

        # Old session gone from both indexes.
        self.assertIsNone(self.reg.get_by_session("old_session"))
        # New session present.
        record = self.reg.get_by_session("new_session")
        self.assertIsNotNone(record)
        self.assertEqual(record.client_id, "c1")

    def test_count_stays_at_one_after_duplicate(self):
        self.reg.register("c1", "s1", "x")
        self.reg.register("c1", "s2", "y")
        self.assertEqual(self.reg.count(), 1)

    def test_client_id_index_points_to_new_session(self):
        self.reg.register("c1", "s1", "x")
        self.reg.register("c1", "s2", "y")
        record = self.reg.get_by_client_id("c1")
        self.assertEqual(record.session_id, "s2")

    def test_remove_new_session_also_clears_client_id_index(self):
        self.reg.register("c1", "s1", "x")
        self.reg.register("c1", "s2", "y")
        self.reg.remove("s2")
        self.assertIsNone(self.reg.get_by_client_id("c1"))


# ---------------------------------------------------------------------------
# ClientRegistry — capacity
# ---------------------------------------------------------------------------

class TestClientRegistryCapacity(unittest.TestCase):

    def test_is_full_false_when_empty(self):
        reg = ClientRegistry(max_clients=3)
        self.assertFalse(reg.is_full())

    def test_is_full_false_when_one_below_limit(self):
        reg = ClientRegistry(max_clients=3)
        reg.register("c1", "s1", "x")
        reg.register("c2", "s2", "y")
        self.assertFalse(reg.is_full())

    def test_is_full_true_at_limit(self):
        reg = ClientRegistry(max_clients=2)
        reg.register("c1", "s1", "x")
        reg.register("c2", "s2", "y")
        self.assertTrue(reg.is_full())

    def test_is_full_false_after_removal_frees_slot(self):
        reg = ClientRegistry(max_clients=1)
        reg.register("c1", "s1", "x")
        self.assertTrue(reg.is_full())
        reg.remove("s1")
        self.assertFalse(reg.is_full())

    def test_capacity_one_allows_one(self):
        reg = ClientRegistry(max_clients=1)
        reg.register("c1", "s1", "x")
        self.assertEqual(reg.count(), 1)


# ---------------------------------------------------------------------------
# ClientRegistry — all_records snapshot
# ---------------------------------------------------------------------------

class TestClientRegistrySnapshot(unittest.TestCase):

    def test_all_records_empty(self):
        reg = ClientRegistry(max_clients=10)
        self.assertEqual(reg.all_records(), [])

    def test_all_records_returns_all(self):
        reg = ClientRegistry(max_clients=10)
        reg.register("c1", "s1", "x")
        reg.register("c2", "s2", "y")
        records = reg.all_records()
        self.assertEqual(len(records), 2)
        ids = {r.client_id for r in records}
        self.assertEqual(ids, {"c1", "c2"})

    def test_all_records_is_snapshot(self):
        """Modifying the registry after taking a snapshot must not affect it."""
        reg = ClientRegistry(max_clients=10)
        reg.register("c1", "s1", "x")
        snapshot = reg.all_records()
        reg.register("c2", "s2", "y")
        self.assertEqual(len(snapshot), 1)


if __name__ == "__main__":
    unittest.main()
