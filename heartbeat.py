"""
heartbeat.py — Background tasks for heartbeat and session timeout.

Two independent asyncio tasks run for the lifetime of the server:

send_heartbeats()
    Wakes every `heartbeat_interval` seconds and sends a HEARTBEAT
    message to every authenticated session.  Connection errors are
    silently swallowed here; the connection reader task will surface
    and handle them when it next tries to receive.

timeout_checker()
    Wakes every `check_interval` seconds and closes any session whose
    ClientRecord has not been touched (last_seen) within `client_timeout`
    seconds.  Sends a DISCONNECT message before closing.

Neither task catches asyncio.CancelledError — the server cancels them
during graceful shutdown and they exit cleanly.
"""
import asyncio
import logging
import time
from typing import TYPE_CHECKING

from protocol import MessageType, build_message

if TYPE_CHECKING:
    from clients import ClientRegistry
    from config import Config
    from sessions import SessionRegistry

logger = logging.getLogger(__name__)


async def send_heartbeats(
    session_registry: "SessionRegistry",
    client_registry: "ClientRegistry",
    config: "Config",
) -> None:
    """
    Periodically send HEARTBEAT to all authenticated clients.

    Sleeps first, then sends, so a newly started server does not
    immediately flood connecting clients.
    """
    heartbeat_msg = build_message(MessageType.HEARTBEAT)

    while True:
        await asyncio.sleep(config.heartbeat_interval)

        sessions = session_registry.all_sessions()
        for session in sessions:
            if not session.authenticated:
                continue
            try:
                await session.websocket.send(heartbeat_msg)
                session.messages_sent += 1
                logger.debug(
                    "HEARTBEAT sent: client_id=%r session_id=%r",
                    session.client_id, session.session_id,
                )
            except Exception as exc:
                # The connection reader will detect and handle this failure.
                logger.debug(
                    "HEARTBEAT send failed (will be cleaned up by reader): "
                    "session_id=%r error=%s",
                    session.session_id, exc,
                )


async def timeout_checker(
    session_registry: "SessionRegistry",
    client_registry: "ClientRegistry",
    config: "Config",
) -> None:
    """
    Evict sessions that have exceeded the client timeout.

    Check interval is the smaller of heartbeat_interval and 15 s so that
    timeouts are detected promptly without busy-polling.
    """
    check_interval = min(config.heartbeat_interval, 15)
    disconnect_msg = build_message(
        MessageType.DISCONNECT,
        payload={"reason": "Session timed out"},
    )

    while True:
        await asyncio.sleep(check_interval)

        now = time.monotonic()
        records = client_registry.all_records()

        for record in records:
            if not record.is_timed_out(config.client_timeout):
                continue

            idle_secs = now - record.last_seen
            logger.warning(
                "Session timed out: client_id=%r session_id=%r idle=%.1fs",
                record.client_id, record.session_id, idle_secs,
            )

            session = session_registry.get(record.session_id)
            if session is None:
                # Already removed by the connection reader; just skip.
                client_registry.remove(record.session_id)
                continue

            try:
                await session.websocket.send(disconnect_msg)
            except Exception:
                pass

            try:
                await session.websocket.close(1001, "Session timed out")
            except Exception:
                pass
            # The connection reader's finally block will call _cleanup_session.
