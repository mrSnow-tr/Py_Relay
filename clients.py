"""
clients.py — In-memory registry of authenticated clients.

A ClientRecord represents the server's view of a logical, persistent
client identity (client_id) tied to its current session (session_id).

Design notes
------------
- Indexed by both session_id (primary) and client_id (secondary) for
  O(1) lookups in both directions without duplicating data.
- When a client reconnects under the same client_id, the old session
  entry is removed and replaced.  The caller is responsible for
  closing the old WebSocket connection before calling register() again.
- All operations are synchronous; safe for single-event-loop use.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Client states
# ---------------------------------------------------------------------------

class ClientState:
    """Lifecycle states for a client record."""
    CONNECTED     = "connected"
    DISCONNECTING = "disconnecting"


# ---------------------------------------------------------------------------
# ClientRecord
# ---------------------------------------------------------------------------

@dataclass
class ClientRecord:
    """
    Server-side record of one authenticated client.

    Immutable fields (set at creation):
        client_id       Persistent logical identity.
        session_id      UUID for this connection attempt.
        remote_address  IP:port string of the WebSocket peer.
        connected_at    monotonic timestamp of successful auth.

    Mutable fields:
        last_seen       Updated on every received message.
        state           Current lifecycle state.
    """
    client_id: str
    session_id: str
    remote_address: str
    connected_at: float
    last_seen: float
    state: str = ClientState.CONNECTED

    def touch(self) -> None:
        """Update last_seen to now.  Called on every received message."""
        self.last_seen = time.monotonic()

    def is_timed_out(self, timeout: int) -> bool:
        """
        Return True if no message has been received within `timeout` seconds.

        Uses monotonic time so clock adjustments do not cause false timeouts.
        """
        return (time.monotonic() - self.last_seen) > timeout


# ---------------------------------------------------------------------------
# ClientRegistry
# ---------------------------------------------------------------------------

class ClientRegistry:
    """
    In-memory registry of currently authenticated clients.

    Maintains two indexes for efficient lookups:
        _by_session:  session_id  → ClientRecord   (primary)
        _by_client:   client_id   → session_id     (secondary; for de-dup)
    """

    def __init__(self, max_clients: int) -> None:
        self._max_clients: int = max_clients
        self._by_session: Dict[str, ClientRecord] = {}
        self._by_client: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Capacity
    # ------------------------------------------------------------------

    def count(self) -> int:
        """Number of currently registered (authenticated) clients."""
        return len(self._by_session)

    def is_full(self) -> bool:
        """True when the registered client count has reached the cap."""
        return len(self._by_session) >= self._max_clients

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get_by_session(self, session_id: str) -> Optional[ClientRecord]:
        """Return the record for a session, or None."""
        return self._by_session.get(session_id)

    def get_by_client_id(self, client_id: str) -> Optional[ClientRecord]:
        """Return the record for a client_id, or None."""
        sid = self._by_client.get(client_id)
        if sid is None:
            return None
        return self._by_session.get(sid)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def register(
        self,
        client_id: str,
        session_id: str,
        remote_address: str,
    ) -> ClientRecord:
        """
        Register a newly-authenticated client.

        If a record for client_id already exists (from a previous session),
        it is removed from the primary index before the new record is added.
        The caller must have already closed the old WebSocket connection.

        Returns the newly created ClientRecord.
        """
        now = time.monotonic()
        record = ClientRecord(
            client_id=client_id,
            session_id=session_id,
            remote_address=remote_address,
            connected_at=now,
            last_seen=now,
            state=ClientState.CONNECTED,
        )

        # Evict any stale entry for the same logical client.
        old_sid = self._by_client.get(client_id)
        if old_sid is not None and old_sid in self._by_session:
            del self._by_session[old_sid]
            logger.debug(
                "Evicted stale session for client_id=%r (old session_id=%r)",
                client_id, old_sid,
            )

        self._by_session[session_id] = record
        self._by_client[client_id] = session_id

        logger.debug(
            "Client registered: client_id=%r session_id=%r remote=%r",
            client_id, session_id, remote_address,
        )
        return record

    def remove(self, session_id: str) -> Optional[ClientRecord]:
        """
        Remove a client record by session_id.

        Returns the removed record, or None if session_id is unknown.
        Also cleans up the secondary client_id index.
        """
        record = self._by_session.pop(session_id, None)
        if record is not None:
            # Only remove the secondary index if it still points to this session.
            if self._by_client.get(record.client_id) == session_id:
                del self._by_client[record.client_id]
            logger.debug(
                "Client removed: client_id=%r session_id=%r",
                record.client_id, session_id,
            )
        return record

    # ------------------------------------------------------------------
    # Iteration
    # ------------------------------------------------------------------

    def all_records(self) -> List[ClientRecord]:
        """Return a snapshot list of all records (safe to iterate while mutating)."""
        return list(self._by_session.values())
