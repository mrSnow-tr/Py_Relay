"""
tests/test_protocol.py

Tests for protocol.py:
- Parsing valid messages
- Detecting all forms of malformed messages
- Protocol version enforcement
- Field type validation
- Size limit enforcement
- build_message serialisation
- Round-trip fidelity
"""
import json
import unittest

from protocol import (
    PROTOCOL_VERSION,
    MessageType,
    ProtocolError,
    build_message,
    new_request_id,
    parse_message,
)

MAX = 65536  # default max_message_size used throughout these tests


class TestParseMessageValid(unittest.TestCase):

    def test_minimal_valid_message(self):
        raw = json.dumps({"version": 1, "type": "hello", "payload": {}})
        msg = parse_message(raw, MAX)
        self.assertEqual(msg.version, 1)
        self.assertEqual(msg.msg_type, "hello")
        self.assertIsNone(msg.request_id)
        self.assertEqual(msg.payload, {})

    def test_with_request_id(self):
        raw = json.dumps({
            "version": 1, "type": "auth",
            "request_id": "req-abc", "payload": {},
        })
        msg = parse_message(raw, MAX)
        self.assertEqual(msg.request_id, "req-abc")

    def test_payload_with_data(self):
        raw = json.dumps({
            "version": 1, "type": "auth",
            "payload": {"client_id": "c1", "credential": "abc123"},
        })
        msg = parse_message(raw, MAX)
        self.assertEqual(msg.payload["client_id"], "c1")

    def test_missing_payload_defaults_to_empty_dict(self):
        raw = json.dumps({"version": 1, "type": "ping"})
        msg = parse_message(raw, MAX)
        self.assertEqual(msg.payload, {})

    def test_unknown_type_is_accepted(self):
        # parse_message does not filter on known types; dispatch does.
        raw = json.dumps({"version": 1, "type": "future_type", "payload": {}})
        msg = parse_message(raw, MAX)
        self.assertEqual(msg.msg_type, "future_type")

    def test_extra_top_level_fields_are_ignored(self):
        raw = json.dumps({
            "version": 1, "type": "ping", "payload": {},
            "unknown_field": "value",
        })
        msg = parse_message(raw, MAX)
        self.assertEqual(msg.msg_type, "ping")


class TestParseMessageInvalidJSON(unittest.TestCase):

    def test_plain_string_raises(self):
        with self.assertRaises(ProtocolError):
            parse_message("not json at all", MAX)

    def test_empty_string_raises(self):
        with self.assertRaises(ProtocolError):
            parse_message("", MAX)

    def test_json_array_raises(self):
        with self.assertRaises(ProtocolError):
            parse_message("[1, 2, 3]", MAX)

    def test_json_null_raises(self):
        with self.assertRaises(ProtocolError):
            parse_message("null", MAX)

    def test_truncated_json_raises(self):
        with self.assertRaises(ProtocolError):
            parse_message('{"version": 1', MAX)


class TestParseMessageVersionField(unittest.TestCase):

    def test_missing_version_raises(self):
        raw = json.dumps({"type": "ping", "payload": {}})
        with self.assertRaises(ProtocolError) as ctx:
            parse_message(raw, MAX)
        self.assertIn("version", str(ctx.exception).lower())

    def test_wrong_version_raises(self):
        raw = json.dumps({"version": 99, "type": "ping", "payload": {}})
        with self.assertRaises(ProtocolError) as ctx:
            parse_message(raw, MAX)
        self.assertIn("99", str(ctx.exception))

    def test_version_as_string_raises(self):
        raw = json.dumps({"version": "1", "type": "ping", "payload": {}})
        with self.assertRaises(ProtocolError):
            parse_message(raw, MAX)

    def test_version_zero_raises(self):
        raw = json.dumps({"version": 0, "type": "ping", "payload": {}})
        with self.assertRaises(ProtocolError):
            parse_message(raw, MAX)

    def test_version_float_raises(self):
        raw = json.dumps({"version": 1.0, "type": "ping", "payload": {}})
        with self.assertRaises(ProtocolError):
            parse_message(raw, MAX)


class TestParseMessageTypeField(unittest.TestCase):

    def test_missing_type_raises(self):
        raw = json.dumps({"version": 1, "payload": {}})
        with self.assertRaises(ProtocolError) as ctx:
            parse_message(raw, MAX)
        self.assertIn("type", str(ctx.exception).lower())

    def test_empty_type_raises(self):
        raw = json.dumps({"version": 1, "type": "", "payload": {}})
        with self.assertRaises(ProtocolError):
            parse_message(raw, MAX)

    def test_integer_type_raises(self):
        raw = json.dumps({"version": 1, "type": 42, "payload": {}})
        with self.assertRaises(ProtocolError):
            parse_message(raw, MAX)

    def test_null_type_raises(self):
        raw = json.dumps({"version": 1, "type": None, "payload": {}})
        with self.assertRaises(ProtocolError):
            parse_message(raw, MAX)


class TestParseMessageRequestIdField(unittest.TestCase):

    def test_null_request_id_is_none(self):
        raw = json.dumps({"version": 1, "type": "ping", "request_id": None, "payload": {}})
        msg = parse_message(raw, MAX)
        self.assertIsNone(msg.request_id)

    def test_integer_request_id_raises(self):
        raw = json.dumps({"version": 1, "type": "ping", "request_id": 123, "payload": {}})
        with self.assertRaises(ProtocolError):
            parse_message(raw, MAX)

    def test_absent_request_id_is_none(self):
        raw = json.dumps({"version": 1, "type": "ping", "payload": {}})
        msg = parse_message(raw, MAX)
        self.assertIsNone(msg.request_id)


class TestParseMessagePayloadField(unittest.TestCase):

    def test_list_payload_raises(self):
        raw = json.dumps({"version": 1, "type": "ping", "payload": [1, 2]})
        with self.assertRaises(ProtocolError):
            parse_message(raw, MAX)

    def test_string_payload_raises(self):
        raw = json.dumps({"version": 1, "type": "ping", "payload": "text"})
        with self.assertRaises(ProtocolError):
            parse_message(raw, MAX)

    def test_integer_payload_raises(self):
        raw = json.dumps({"version": 1, "type": "ping", "payload": 0})
        with self.assertRaises(ProtocolError):
            parse_message(raw, MAX)


class TestParseMessageSizeLimit(unittest.TestCase):

    def test_message_at_limit_is_accepted(self):
        # Build a message that is exactly at the limit.
        base = json.dumps({"version": 1, "type": "ping", "payload": {}})
        parse_message(base, len(base.encode("utf-8")))  # should not raise

    def test_message_over_limit_raises(self):
        raw = json.dumps({"version": 1, "type": "ping", "payload": {}})
        limit = len(raw.encode("utf-8")) - 1
        with self.assertRaises(ProtocolError) as ctx:
            parse_message(raw, limit)
        self.assertIn("size", str(ctx.exception).lower())

    def test_tiny_limit_raises(self):
        with self.assertRaises(ProtocolError):
            parse_message('{"version":1,"type":"ping","payload":{}}', 5)


class TestBuildMessage(unittest.TestCase):

    def test_builds_valid_json(self):
        raw = build_message(MessageType.PING)
        data = json.loads(raw)
        self.assertEqual(data["version"], PROTOCOL_VERSION)
        self.assertEqual(data["type"], MessageType.PING)
        self.assertEqual(data["payload"], {})

    def test_no_request_id_key_when_absent(self):
        raw = build_message(MessageType.PING)
        data = json.loads(raw)
        self.assertNotIn("request_id", data)

    def test_request_id_present_when_given(self):
        raw = build_message(MessageType.PONG, request_id="r1")
        data = json.loads(raw)
        self.assertEqual(data["request_id"], "r1")

    def test_payload_included(self):
        raw = build_message(MessageType.AUTH_OK, payload={"session_id": "s1"})
        data = json.loads(raw)
        self.assertEqual(data["payload"]["session_id"], "s1")

    def test_none_payload_becomes_empty_dict(self):
        raw = build_message(MessageType.PING, payload=None)
        data = json.loads(raw)
        self.assertEqual(data["payload"], {})

    def test_compact_separators(self):
        raw = build_message(MessageType.PING)
        # Should not contain ": " or ", " (compact form).
        self.assertNotIn(": ", raw)
        self.assertNotIn(", ", raw)


class TestRoundTrip(unittest.TestCase):

    def _roundtrip(self, msg_type: str, payload: dict, request_id: str = None):
        raw = build_message(msg_type, payload=payload, request_id=request_id)
        return parse_message(raw, MAX)

    def test_hello_roundtrip(self):
        msg = self._roundtrip(MessageType.HELLO, {"nonce": "abc", "relay": "r"})
        self.assertEqual(msg.msg_type, MessageType.HELLO)
        self.assertEqual(msg.payload["nonce"], "abc")

    def test_auth_roundtrip_with_request_id(self):
        msg = self._roundtrip(
            MessageType.AUTH,
            {"client_id": "c1", "credential": "deadbeef"},
            request_id="req-42",
        )
        self.assertEqual(msg.request_id, "req-42")
        self.assertEqual(msg.payload["client_id"], "c1")

    def test_error_roundtrip(self):
        msg = self._roundtrip(MessageType.ERROR, {"reason": "something failed"})
        self.assertEqual(msg.payload["reason"], "something failed")


class TestNewRequestId(unittest.TestCase):

    def test_returns_string(self):
        self.assertIsInstance(new_request_id(), str)

    def test_ids_are_unique(self):
        ids = {new_request_id() for _ in range(200)}
        self.assertEqual(len(ids), 200)

    def test_id_is_nonempty(self):
        self.assertTrue(new_request_id())


if __name__ == "__main__":
    unittest.main()
