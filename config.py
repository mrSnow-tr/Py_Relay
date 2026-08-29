"""
config.py — Centralised configuration via environment variables.

Fails at startup with a clear message if AUTH_SECRET is absent.
Secrets are never logged.
"""
import logging
import os
import sys
from dataclasses import dataclass
from typing import List

# Variables that must be present for the server to start.
_REQUIRED: List[str] = ["AUTH_SECRET"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Config:
    """
    Immutable configuration snapshot loaded once at startup.

    Attributes
    ----------
    host                Bind address (default 0.0.0.0)
    port                Listening port — must come from $PORT on Render
    log_level           Python logging level name (DEBUG/INFO/WARNING/ERROR)
    relay_name          Human-readable name reported in HELLO and /health
    protocol_version    Protocol version enforced during message validation
    auth_secret         Shared HMAC secret; NEVER logged or transmitted
    heartbeat_interval  Seconds between server→client HEARTBEAT messages
    auth_timeout        Seconds a new connection has to complete authentication
    client_timeout      Seconds without any message before a session is evicted
    max_clients         Hard cap on simultaneously authenticated clients
    max_message_size    WebSocket message size cap in bytes (default 64 KiB)
    """

    host: str
    port: int
    log_level: str
    relay_name: str
    protocol_version: int
    auth_secret: str          # never surfaced in logs
    heartbeat_interval: int
    auth_timeout: int
    client_timeout: int
    max_clients: int
    max_message_size: int


def load_config() -> Config:
    """
    Build a Config from environment variables.

    Exits with status 1 if any required variable is missing.
    All optional variables have safe, conservative defaults.
    """
    missing = [v for v in _REQUIRED if not os.environ.get(v)]
    if missing:
        # Use print here because logging may not be configured yet.
        print(
            f"[FATAL] Missing required environment variable(s): "
            f"{', '.join(missing)}. "
            f"Set AUTH_SECRET to a long random secret before starting.",
            file=sys.stderr,
        )
        sys.exit(1)

    return Config(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        relay_name=os.environ.get("RELAY_NAME", "py_relay"),
        protocol_version=int(os.environ.get("PROTOCOL_VERSION", "1")),
        auth_secret=os.environ["AUTH_SECRET"],          # required; never log
        heartbeat_interval=int(os.environ.get("HEARTBEAT_INTERVAL", "25")),
        auth_timeout=int(os.environ.get("AUTH_TIMEOUT", "15")),
        client_timeout=int(os.environ.get("CLIENT_TIMEOUT", "90")),
        max_clients=int(os.environ.get("MAX_CLIENTS", "100")),
        max_message_size=int(os.environ.get("MAX_MESSAGE_SIZE", str(64 * 1024))),
    )
