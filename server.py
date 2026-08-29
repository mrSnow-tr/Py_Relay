"""
server.py — Core relay server.

RelayServer owns:
- The websockets.serve() instance
- ClientRegistry + SessionRegistry
- Lightweight Metrics counters
- Per-connection lifecycle (auth → message loop → cleanup)
- Background task management (heartbeat, timeout)
- Graceful shutdown

HTTP health endpoint (/health) is handled inside _process_http_request(),
which is passed to websockets as the process_request callback.  All other
paths return 404.  The WebSocket path "/" proceeds to upgrade.

Connection lifecycle
--------------------
    WebSocket connect
         │
         ▼
    _handle_connection()      ← one task per connection
         │
         ├─ create Session, send to SessionRegistry
         │
         ├─ _run_auth_phase()
         │       send HELLO (nonce)
         │       wait for AUTH (with auth_timeout)
         │       verify credential
         │       handle duplicate session
         │       register in ClientRegistry
         │       send AUTH_OK
         │
         ├─ _run_message_loop()
         │       receive messages
         │       update last_seen on each message
         │       dispatch to _on_heartbeat / _on_ping / _on_disconnect
         │
         └─ _cleanup_session()   ← always runs (finally block)
               remove from SessionRegistry
               remove from ClientRegistry
               log disconnect
"""
import asyncio
import http
import json
import logging
import time
import uuid
from typing import Any, Dict, Optional, Set

import websockets
import websockets.exceptions

from auth import generate_nonce, validate_client_id, verify_credential
from clients import ClientRegistry
from config import Config
from heartbeat import send_heartbeats, timeout_checker
from protocol import (
    Message,
    MessageType,
    ProtocolError,
    build_message,
    parse_message,
)
from sessions import Session, SessionRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class Metrics:
    """
    Lightweight server-wide counters.

    Not thread-safe by design — all access is from the single asyncio
    event loop.  Use simple integer addition; no locks needed.
    """

    def __init__(self) -> None:
        self._start: float = time.monotonic()
        self.total_connections: int = 0
        self.auth_failures: int = 0
        self.protocol_errors: int = 0
        self.messages_received: int = 0
        self.messages_sent: int = 0

    @property
    def uptime(self) -> float:
        """Seconds since the server started."""
        return time.monotonic() - self._start


# ---------------------------------------------------------------------------
# RelayServer
# ---------------------------------------------------------------------------

class RelayServer:
    """
    Orchestrates the WebSocket relay.

    Usage
    -----
        stop = asyncio.Event()
        server = RelayServer(config)
        await server.run(stop)     # blocks until stop.set()
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.client_registry = ClientRegistry(config.max_clients)
        self.session_registry = SessionRegistry()
        self.metrics = Metrics()
        self._background_tasks: Set[asyncio.Task] = set()  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self, stop_event: asyncio.Event) -> None:
        """
        Start the server and block until stop_event is set.

        Starts background heartbeat/timeout tasks, opens the WebSocket
        listener, waits for shutdown, then notifies clients and cleans up.
        """
        logger.info(
            "py_relay starting: name=%r protocol_version=%d host=%s port=%d",
            self.config.relay_name,
            self.config.protocol_version,
            self.config.host,
            self.config.port,
        )
        logger.info(
            "Limits: max_clients=%d max_message_size=%d "
            "heartbeat_interval=%ds auth_timeout=%ds client_timeout=%ds",
            self.config.max_clients,
            self.config.max_message_size,
            self.config.heartbeat_interval,
            self.config.auth_timeout,
            self.config.client_timeout,
        )

        # Start background tasks.
        hb_task = asyncio.create_task(
            send_heartbeats(self.session_registry, self.client_registry, self.config),
            name="heartbeat_sender",
        )
        to_task = asyncio.create_task(
            timeout_checker(self.session_registry, self.client_registry, self.config),
            name="timeout_checker",
        )
        self._background_tasks = {hb_task, to_task}

        # Open the WebSocket server.
        async with websockets.serve(
            self._handle_connection,
            self.config.host,
            self.config.port,
            process_request=self._process_http_request,
            # Delegate message size enforcement to our own parse_message();
            # set max_size here as a hard transport-level cap.
            max_size=self.config.max_message_size,
            # We run our own heartbeat; disable websockets' built-in ping.
            ping_interval=None,
            ping_timeout=None,
        ):
            logger.info(
                "Listening on %s:%d — /health and ws:// ready",
                self.config.host,
                self.config.port,
            )
            await stop_event.wait()

        # --- graceful shutdown ---
        logger.info("Shutdown: notifying %d client(s)...", self.session_registry.count())
        await self._notify_shutdown()

        for task in self._background_tasks:
            task.cancel()
        await asyncio.gather(*self._background_tasks, return_exceptions=True)

        logger.info("py_relay stopped cleanly.")

    # ------------------------------------------------------------------
    # HTTP handler (health endpoint)
    # ------------------------------------------------------------------

    async def _process_http_request(
        self, path: str, headers: Any
    ) -> Optional[Any]:
        """
        Handle plain HTTP requests before the WebSocket upgrade.

        Returns None to proceed with the WebSocket handshake (path "/"),
        or an HTTP response tuple for all other paths.
        """
        if path == "/health":
            body = json.dumps(
                {
                    "status": "ok",
                    "relay": self.config.relay_name,
                    "uptime": int(self.metrics.uptime),
                    "clients": self.client_registry.count(),
                },
                separators=(",", ":"),
            ).encode("utf-8")
            return (
                http.HTTPStatus.OK,
                [
                    ("Content-Type", "application/json"),
                    ("Content-Length", str(len(body))),
                ],
                body,
            )

        if path == "/" or path == "":
            # Proceed with WebSocket upgrade.
            return None

        return (
            http.HTTPStatus.NOT_FOUND,
            [("Content-Type", "text/plain")],
            b"Not Found",
        )

    # ------------------------------------------------------------------
    # Connection handler
    # ------------------------------------------------------------------

    async def _handle_connection(self, websocket: Any, path: str) -> None:
        """
        Handle the full lifecycle of one WebSocket connection.

        Any exception that escapes is caught here so it cannot affect
        other connections.
        """
        remote = _remote_address(websocket)
        session_id = str(uuid.uuid4())
        nonce = generate_nonce()

        session = Session(
            session_id=session_id,
            websocket=websocket,
            remote_address=remote,
            nonce=nonce,
            created_at=time.monotonic(),
        )
        self.session_registry.add(session)
        self.metrics.total_connections += 1

        logger.info(
            "Connection opened: session_id=%r remote=%r (open sessions: %d)",
            session_id, remote, self.session_registry.count(),
        )

        try:
            authenticated = await self._run_auth_phase(session)
            if authenticated:
                await self._run_message_loop(session)

        except (
            websockets.exceptions.ConnectionClosedOK,
            websockets.exceptions.ConnectionClosedError,
        ) as exc:
            logger.info(
                "Connection closed: session_id=%r code=%s",
                session_id,
                getattr(exc, "code", "?"),
            )
        except asyncio.CancelledError:
            raise  # Let asyncio handle task cancellation normally.
        except Exception:
            logger.exception(
                "Unexpected error on session_id=%r — connection will be closed.",
                session_id,
            )
        finally:
            await self._cleanup_session(session)

    # ------------------------------------------------------------------
    # Auth phase
    # ------------------------------------------------------------------

    async def _run_auth_phase(self, session: Session) -> bool:
        """
        Run the authentication handshake for a new connection.

        Returns True if the client authenticated successfully.
        Returns False (and closes the connection) on any failure.
        """
        # Reject if server is at capacity.
        if self.client_registry.is_full():
            await self._send_error(
                session, "Server is at maximum client capacity", close=True
            )
            logger.warning(
                "Connection rejected (at capacity): session_id=%r remote=%r",
                session.session_id, session.remote_address,
            )
            return False

        # Send HELLO with the per-connection nonce.
        hello = build_message(
            MessageType.HELLO,
            payload={
                "relay": self.config.relay_name,
                "version": self.config.protocol_version,
                "nonce": session.nonce,
            },
        )
        try:
            await self._send(session, hello)
        except Exception as exc:
            logger.warning(
                "Failed to send HELLO: session_id=%r error=%s",
                session.session_id, exc,
            )
            return False

        # Wait for the AUTH message, with a deadline.
        try:
            raw = await asyncio.wait_for(
                session.websocket.recv(),
                timeout=self.config.auth_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Auth timeout: session_id=%r remote=%r",
                session.session_id, session.remote_address,
            )
            try:
                await session.websocket.close(1008, "Authentication timeout")
            except Exception:
                pass
            return False
        except websockets.exceptions.ConnectionClosed:
            logger.info(
                "Connection closed before auth: session_id=%r",
                session.session_id,
            )
            return False

        session.messages_received += 1
        self.metrics.messages_received += 1

        # Parse and validate the incoming message.
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                await self._send_auth_failed(session, "Message is not valid UTF-8")
                return False

        try:
            message = parse_message(raw, self.config.max_message_size)
        except ProtocolError as exc:
            logger.warning(
                "Protocol error during auth: session_id=%r error=%s",
                session.session_id, exc,
            )
            self.metrics.protocol_errors += 1
            await self._send_auth_failed(session, "Protocol error")
            return False

        if message.msg_type != MessageType.AUTH:
            logger.warning(
                "Expected AUTH, got %r: session_id=%r",
                message.msg_type, session.session_id,
            )
            await self._send_auth_failed(
                session, "First message must be AUTH", request_id=message.request_id
            )
            return False

        # Extract auth fields.
        client_id = message.payload.get("client_id")
        credential = message.payload.get("credential")

        if not isinstance(client_id, str) or not isinstance(credential, str):
            self.metrics.auth_failures += 1
            await self._send_auth_failed(
                session,
                "AUTH payload must contain string fields 'client_id' and 'credential'",
                request_id=message.request_id,
            )
            return False

        # Validate client_id format.
        valid, reason = validate_client_id(client_id)
        if not valid:
            self.metrics.auth_failures += 1
            await self._send_auth_failed(
                session, f"Invalid client_id: {reason}", request_id=message.request_id
            )
            return False

        # Verify the HMAC credential.
        if not verify_credential(
            session.nonce, client_id, credential, self.config.auth_secret
        ):
            # Log client_id but never the credential or secret.
            logger.warning(
                "Authentication failed: session_id=%r client_id=%r remote=%r",
                session.session_id, client_id, session.remote_address,
            )
            self.metrics.auth_failures += 1
            await self._send_auth_failed(
                session, "Invalid credential", request_id=message.request_id
            )
            return False

        # Handle duplicate session: same client_id connecting again.
        existing_session = self.session_registry.get_by_client_id(client_id)
        if existing_session is not None:
            logger.info(
                "Duplicate client: client_id=%r — invalidating old session_id=%r, "
                "accepting new session_id=%r",
                client_id, existing_session.session_id, session.session_id,
            )
            await self._invalidate_session(
                existing_session, "Replaced by a new connection"
            )

        # Mark session as authenticated.
        session.client_id = client_id
        session.authenticated = True

        # Register in the client registry.
        self.client_registry.register(
            client_id=client_id,
            session_id=session.session_id,
            remote_address=session.remote_address,
        )

        # Send AUTH_OK.
        auth_ok = build_message(
            MessageType.AUTH_OK,
            payload={"session_id": session.session_id},
            request_id=message.request_id,
        )
        await self._send(session, auth_ok)

        logger.info(
            "Client authenticated: client_id=%r session_id=%r remote=%r "
            "(active clients: %d)",
            client_id, session.session_id, session.remote_address,
            self.client_registry.count(),
        )
        return True

    # ------------------------------------------------------------------
    # Message loop
    # ------------------------------------------------------------------

    async def _run_message_loop(self, session: Session) -> None:
        """
        Read and dispatch messages for an authenticated session.

        Iterates until the WebSocket closes or an exception propagates.
        """
        async for raw in session.websocket:
            # Decode bytes if needed.
            if isinstance(raw, bytes):
                try:
                    raw = raw.decode("utf-8")
                except UnicodeDecodeError:
                    logger.warning(
                        "Non-UTF-8 binary message: session_id=%r", session.session_id
                    )
                    self.metrics.protocol_errors += 1
                    await self._send_error(session, "Message must be UTF-8 text")
                    continue

            # Size check (belt-and-suspenders; websockets also enforces max_size).
            if len(raw.encode("utf-8")) > self.config.max_message_size:
                logger.warning(
                    "Oversized message: session_id=%r size=%d limit=%d",
                    session.session_id,
                    len(raw),
                    self.config.max_message_size,
                )
                self.metrics.protocol_errors += 1
                await self._send_error(session, "Message exceeds size limit")
                continue

            session.messages_received += 1
            self.metrics.messages_received += 1

            # Parse.
            try:
                message = parse_message(raw, self.config.max_message_size)
            except ProtocolError as exc:
                logger.warning(
                    "Protocol error: session_id=%r client_id=%r error=%s",
                    session.session_id, session.client_id, exc,
                )
                self.metrics.protocol_errors += 1
                await self._send_error(session, "Protocol error")
                continue

            # Update client's last_seen timestamp.
            record = self.client_registry.get_by_session(session.session_id)
            if record is not None:
                record.touch()

            # Dispatch.
            await self._dispatch(session, message)

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, session: Session, message: Message) -> None:
        """Route a parsed message to the correct handler."""
        t = message.msg_type

        if t == MessageType.HEARTBEAT:
            await self._on_heartbeat(session, message)
        elif t == MessageType.PING:
            await self._on_ping(session, message)
        elif t == MessageType.DISCONNECT:
            await self._on_disconnect(session, message)
        else:
            # Unknown or server-to-client type received from a client.
            logger.warning(
                "Unexpected message type %r from client: session_id=%r client_id=%r",
                t, session.session_id, session.client_id,
            )
            self.metrics.protocol_errors += 1
            await self._send_error(
                session,
                f"Unexpected message type: {t!r}",
                request_id=message.request_id,
            )

    async def _on_heartbeat(self, session: Session, message: Message) -> None:
        ack = build_message(MessageType.HEARTBEAT_ACK, request_id=message.request_id)
        await self._send(session, ack)
        logger.debug(
            "HEARTBEAT ack: client_id=%r session_id=%r",
            session.client_id, session.session_id,
        )

    async def _on_ping(self, session: Session, message: Message) -> None:
        pong = build_message(MessageType.PONG, request_id=message.request_id)
        await self._send(session, pong)
        logger.debug(
            "PING/PONG: client_id=%r session_id=%r",
            session.client_id, session.session_id,
        )

    async def _on_disconnect(self, session: Session, message: Message) -> None:
        reason = message.payload.get("reason", "Client requested disconnect")
        logger.info(
            "Graceful disconnect: client_id=%r reason=%r",
            session.client_id, reason,
        )
        try:
            await session.websocket.close(1000, "Goodbye")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Send helpers
    # ------------------------------------------------------------------

    async def _send(self, session: Session, data: str) -> None:
        """Send a message string to a session, ignoring already-closed connections."""
        try:
            await session.websocket.send(data)
            session.messages_sent += 1
            self.metrics.messages_sent += 1
        except websockets.exceptions.ConnectionClosed:
            pass

    async def _send_error(
        self,
        session: Session,
        reason: str,
        request_id: Optional[str] = None,
        close: bool = False,
    ) -> None:
        msg = build_message(
            MessageType.ERROR,
            payload={"reason": reason},
            request_id=request_id,
        )
        await self._send(session, msg)
        if close:
            try:
                await session.websocket.close(1008, reason[:120])
            except Exception:
                pass

    async def _send_auth_failed(
        self,
        session: Session,
        reason: str,
        request_id: Optional[str] = None,
    ) -> None:
        msg = build_message(
            MessageType.AUTH_FAILED,
            payload={"reason": reason},
            request_id=request_id,
        )
        await self._send(session, msg)
        try:
            await session.websocket.close(1008, "Authentication failed")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Session lifecycle helpers
    # ------------------------------------------------------------------

    async def _invalidate_session(self, session: Session, reason: str) -> None:
        """Send DISCONNECT and close an existing session being replaced."""
        msg = build_message(
            MessageType.DISCONNECT,
            payload={"reason": reason},
        )
        try:
            await session.websocket.send(msg)
        except Exception:
            pass
        try:
            await session.websocket.close(1001, reason[:120])
        except Exception:
            pass

    async def _cleanup_session(self, session: Session) -> None:
        """
        Remove a session from all registries.

        Always called from the finally block of _handle_connection so
        it runs regardless of how the connection ended.
        """
        self.session_registry.remove(session.session_id)

        if session.authenticated and session.client_id:
            # Only remove the ClientRecord if it still belongs to this session.
            record = self.client_registry.get_by_session(session.session_id)
            if record is not None:
                self.client_registry.remove(session.session_id)

            logger.info(
                "Client disconnected: client_id=%r session_id=%r "
                "msgs_rx=%d msgs_tx=%d (active clients: %d)",
                session.client_id,
                session.session_id,
                session.messages_received,
                session.messages_sent,
                self.client_registry.count(),
            )
        else:
            logger.info(
                "Pre-auth session closed: session_id=%r remote=%r",
                session.session_id, session.remote_address,
            )

    async def _notify_shutdown(self) -> None:
        """Notify all connected clients that the server is shutting down."""
        sessions = self.session_registry.all_sessions()
        if not sessions:
            return

        disconnect_msg = build_message(
            MessageType.DISCONNECT,
            payload={"reason": "Server is shutting down"},
        )

        async def _close_one(s: Session) -> None:
            try:
                await s.websocket.send(disconnect_msg)
            except Exception:
                pass
            try:
                await s.websocket.close(1001, "Server shutting down")
            except Exception:
                pass

        await asyncio.gather(*[_close_one(s) for s in sessions], return_exceptions=True)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _remote_address(websocket: Any) -> str:
    """Extract a printable remote address from a WebSocket connection."""
    try:
        remote = websocket.remote_address
        if remote:
            return f"{remote[0]}:{remote[1]}"
    except Exception:
        pass
    return "unknown"
