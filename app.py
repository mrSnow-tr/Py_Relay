"""
app.py — py_relay entry point.

Loads configuration, configures logging, registers OS signal handlers
for graceful shutdown, and runs the relay server.

Usage
-----
    python app.py
    python -m py_relay        # if run as a package via __main__.py
"""
import asyncio
import logging
import signal
import sys

from config import load_config
from logging_config import setup_logging
from server import RelayServer

logger = logging.getLogger(__name__)


async def _run() -> None:
    """Async entry point: configure, start, and run until a stop signal."""
    config = load_config()
    setup_logging(config.log_level)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        if not stop_event.is_set():
            logger.info("Shutdown signal received — stopping server.")
            stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except (NotImplementedError, RuntimeError):
            # Windows does not support add_signal_handler.
            # SIGINT will surface as KeyboardInterrupt and is caught below.
            pass

    relay = RelayServer(config)
    await relay.run(stop_event)


def main() -> None:
    """Synchronous entry point called from the command line."""
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        # Happens on Windows (no add_signal_handler) or Ctrl-C in development.
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
