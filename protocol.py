"""
protocol.py — V1 JSON control-plane protocol.

All message construction and parsing goes through this module.
Nothing outside this module should build or decode raw protocol bytes.

Wire format
-----------
Every message is a UTF-8 JSON object:

    {
        "version":    1,
        "type":       "<message_type>",
        "request_id": "<uuid-string>",   // optional
        "payload":    {}                 // always an object
    }

Message types
-------------
Implemented (V1):
    hello          Server → Client after TCP/WebSocket connection
    auth           Client → Server  authentication request
    auth_ok        Server → Client  authentication accepted
    auth_failed    Server → Client  authentication rejected
    heartbeat      Either direction keep-alive probe
    heartbeat_ack  Response to heartbeat
    ping           Either direction connectivity check
    pong           Response to ping
    disconnect     Either direction graceful close notification
    error          Server → Client  protocol / server error

Reserved for future releases (not implemented):
    session_resume, client_info, peer_list, connect_peer, data, route
"""
import json
import uuid
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Protocol version
# ---------------------------------------------------------------------------

PROTOCOL_VERSION: int = 1


# ---------------------------------------------------------------------------
# Message type constants
# ---------------------------------------------------------------------------

class MessageType:
    """String constants for all protocol message types."""

    # V1 — implemented
    HELLO         = "hello"
    AUTH          = "auth"
    AUTH_OK       = "auth_ok"
    AUTH_FAILED   = "auth_failed"
    HEARTBEAT     = "heartbeat"
    HEARTBEAT_ACK = "heartbeat_ack"
    PING          = "ping"
    PONG          = "pong"
    DISCONNECT    = "disconnect"
    ERROR         = "error"

    # Reserved — not implemented in V1
    SESSION_RESUME = "session_resume"
    CLIENT_INFO    = "client_info"
    PEER_LIST      = "peer_list"
    CONNECT_PEER   = "connect_peer"
    DATA           = "data"
    ROUTE          = "route"

    # All types that are valid to receive from clients in V1.
    CLIENT_MESSAGES = frozenset({
        AUTH, HEARTBEAT, PING, DISCONNECT,
    })

    # All types valid in V1 (used for version-awareness checks).
    ALL_V1 = frozenset({
        HELLO, AUTH, AUTH_OK, AUTH_FAILED,
        HEARTBEAT, HEARTBEAT_ACK,
        PING, PONG,
        DISCONNECT, ERROR,
    })


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ProtocolError(Exception):
    """Raised when an incoming message violates the protocol."""


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

class Message:
    """A parsed, validated protocol message."""

    __slots__ = ("version", "msg_type", "request_id", "payload")

    def __init__(
        self,
        version: int,
        msg_type: str,
        request_id: Optional[str],
        payload: Dict[str, Any],
    ) -> None:
        self.version = version
        self.msg_type = msg_type
        self.request_id = request_id
        self.payload = payload

    def __repr__(self) -> str:
        return (
            f"Message(version={self.version}, "
            f"type={self.msg_type!r}, "
            f"request_id={self.request_id!r})"
        )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_message(raw: str, max_size: int) -> Message:
    """
    Parse and validate a raw UTF-8 string as a protocol message.

    Parameters
    ----------
    raw:      The raw text received from the WebSocket.
    max_size: Maximum allowed byte length.  Messages larger than this
              are rejected even if they are valid JSON.

    Returns
    -------
    A validated Message instance.

    Raises
    ------
    ProtocolError for any validation failure (bad JSON, wrong version,
    missing fields, wrong field types, size excess).
    """
    # Size guard — belt-and-suspenders alongside websockets' own max_size.
    if len(raw.encode("utf-8")) > max_size:
        raise ProtocolError(
            f"Message exceeds size limit ({len(raw)} > {max_size} bytes)"
        )

    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"Invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ProtocolError("Message must be a JSON object, got a different type")

    # version
    version = data.get("version")
    if version is None:
        raise ProtocolError("Missing required field 'version'")
    if not isinstance(version, int):
        raise ProtocolError("Field 'version' must be an integer")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            f"Unsupported protocol version {version}; "
            f"this relay requires version {PROTOCOL_VERSION}"
        )

    # type
    msg_type = data.get("type")
    if msg_type is None:
        raise ProtocolError("Missing required field 'type'")
    if not isinstance(msg_type, str):
        raise ProtocolError("Field 'type' must be a string")
    if not msg_type:
        raise ProtocolError("Field 'type' must not be empty")

    # request_id  (optional)
    request_id = data.get("request_id")
    if request_id is not None and not isinstance(request_id, str):
        raise ProtocolError("Field 'request_id' must be a string when present")

    # payload  (optional; defaults to empty object)
    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        raise ProtocolError("Field 'payload' must be a JSON object")

    return Message(
        version=version,
        msg_type=msg_type,
        request_id=request_id,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def build_message(
    msg_type: str,
    payload: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
    version: int = PROTOCOL_VERSION,
) -> str:
    """
    Serialise a protocol message to a compact UTF-8 JSON string.

    Parameters
    ----------
    msg_type:   One of the MessageType constants.
    payload:    Optional dict to include as the payload object.
    request_id: Optional correlation identifier.
    version:    Protocol version (defaults to PROTOCOL_VERSION).

    Returns
    -------
    A compact JSON string ready to send over a WebSocket.
    """
    data: Dict[str, Any] = {
        "version": version,
        "type": msg_type,
    }
    if request_id is not None:
        data["request_id"] = request_id
    data["payload"] = payload if payload is not None else {}
    return json.dumps(data, separators=(",", ":"))


def new_request_id() -> str:
    """Generate a unique, opaque request correlation identifier."""
    return str(uuid.uuid4())
