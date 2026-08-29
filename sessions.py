"""
sessions.py — In-memory registry of all active WebSocket sessions.

Distinction between Session and ClientRecord
--------------------------------------------
Session:        Exists from the moment a WebSocket connection is opened,
                even before authentication.  Holds the live WebSocket object.
                Removed when the connection closes for any reason.

ClientRecord:   Exists only after authentication succeeds.  Represents the
                logical, authenticated client identity.  Stored separately
                in ClientRegistry so heartbeat/timeout logic does not need
                to touch WebSocket objects directly.

All operations are synchronous; safe for single-event-loop use.
"""
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Session:
    """
    One active WebSocket session, authenticated or not.

    Fields set at connection time (immutable after creation):
        session_id      Unique UUID for this connection.
        websocket       The live websockets.WebSocketServerProtocol.
        remote_address  "ip:port" string.
        nonce           Server-generated challenge sent in HELLO.
        created_at      Monotonic timestamp.

    Fields updated during authentication:
        client_id       Set after successful AUTH.  None until then.
        authenticated   True only after AUTH_OK is sent.

    Lightweight per-session counters (for diagnostics):
        messages_received
        messages_sent
    """
    session_id: str
    websocket: Any          # websockets.WebSocketServerProtocol
    remote_address: str
    nonce: str
    created_at: float
    client_id: Optional[str] = None
    authenticated: bool = False
    messages_received: int = 0
    messages_sent: int = 0


class SessionRegistry:
    """
    In-memory registry of all active WebSocket sessions.

    Keyed by session_id.  Also supports lookup by client_id for
    duplicate-session detection (authenticated sessions only).
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, Session] = {}

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def add(self, session: Session) -> None:
        """Register a new session.  Overwrites any existing entry with the same ID."""
        self._sessions[session.session_id] = session

    def remove(self, session_id: str) -> Optional[Session]:
        """Remove and return a session, or None if not found."""
        return self._sessions.pop(session_id, None)

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get(self, session_id: str) -> Optional[Session]:
        """Return a session by its ID, or None."""
        return self._sessions.get(session_id)

    def get_by_client_id(self, client_id: str) -> Optional[Session]:
        """
        Return the authenticated session for a given client_id, or None.

        Searches only sessions where authenticated=True so pre-auth
        connections that happen to share a client_id (race conditions)
        are not mistakenly matched.
        """
        for session in self._sessions.values():
            if session.authenticated and session.client_id == client_id:
                return session
        return None

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def count(self) -> int:
        """Total number of open sessions (includes pre-auth)."""
        return len(self._sessions)

    def all_sessions(self) -> List[Session]:
        """Return a snapshot list of all sessions (safe to iterate while mutating)."""
        return list(self._sessions.values())
