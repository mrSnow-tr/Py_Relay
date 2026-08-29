"""
logging_config.py — Logging setup for py_relay.

Configures the root logger with a structured, human-readable format
suitable for both local development and Render's log aggregator.

Format
------
    2024-01-15 12:34:56,789 INFO     server       Client authenticated: ...
    2024-01-15 12:34:56,790 WARNING  heartbeat    Session timed out: ...

Log levels
----------
Controlled by the LOG_LEVEL environment variable (via config.py).
Default: INFO

What is NOT logged
------------------
- AUTH_SECRET or any part of it
- Client credentials or nonces
- Arbitrary message payloads
- Session tokens

See the logging calls in each module for what IS logged.
"""
import logging


_FORMAT = "%(asctime)s %(levelname)-8s %(name)-14s %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str) -> None:
    """
    Configure the root logger.

    Parameters
    ----------
    level:  A logging level name such as "DEBUG", "INFO", "WARNING", "ERROR".
            Unknown names default to INFO with a warning.
    """
    numeric_level = getattr(logging, level, None)
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO
        # Can't log this yet — use print before the root logger is ready.
        print(f"[WARNING] Unknown LOG_LEVEL={level!r}; defaulting to INFO.")

    logging.basicConfig(
        level=numeric_level,
        format=_FORMAT,
        datefmt=_DATE_FORMAT,
    )

    # Quiet noisy third-party loggers that we don't control.
    logging.getLogger("websockets").setLevel(logging.WARNING)
