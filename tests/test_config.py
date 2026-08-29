"""
tests/test_config.py

Tests for config.py:
- Missing AUTH_SECRET causes immediate exit with status 1
- Empty AUTH_SECRET is treated as missing
- Valid configuration loads and returns a Config
- Default values are applied for every optional variable
- Custom env values override defaults
- AUTH_SECRET is stored on the Config but never exposed in log output
- Config object is immutable (frozen dataclass)
"""
import logging
import os
import sys
import unittest
from io import StringIO
from unittest.mock import patch

# Ensure the project root is on the path (mirrors conftest.py).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import load_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(**overrides):
    """
    Build a minimal environment dict that satisfies load_config() requirements.

    By default only AUTH_SECRET is set; pass keyword arguments to override
    or add additional variables.  Pass ``None`` as a value to omit a key.
    """
    base = {"AUTH_SECRET": "test-secret-for-unit-tests"}
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


# ---------------------------------------------------------------------------
# Missing / empty secret
# ---------------------------------------------------------------------------

class TestMissingSecret(unittest.TestCase):

    def test_missing_auth_secret_exits_with_status_1(self):
        """
        If AUTH_SECRET is absent from the environment, load_config()
        must print a message to stderr and exit with status 1.
        """
        env = {k: v for k, v in os.environ.items() if k != "AUTH_SECRET"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                load_config()
        self.assertEqual(ctx.exception.code, 1)

    def test_empty_auth_secret_exits_with_status_1(self):
        """
        An empty-string AUTH_SECRET is falsy and must also cause exit.
        """
        env = {k: v for k, v in os.environ.items()}
        env["AUTH_SECRET"] = ""
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                load_config()
        self.assertEqual(ctx.exception.code, 1)

    def test_missing_secret_prints_to_stderr(self):
        """
        The fatal message must go to stderr, not stdout, so container
        log collectors (including Render) can distinguish it from
        normal application output.
        """
        env = {k: v for k, v in os.environ.items() if k != "AUTH_SECRET"}
        with patch.dict(os.environ, env, clear=True):
            with patch("sys.stderr", new_callable=StringIO) as mock_err:
                try:
                    load_config()
                except SystemExit:
                    pass
            output = mock_err.getvalue()
        self.assertIn("AUTH_SECRET", output)


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------

class TestConfigDefaults(unittest.TestCase):
    """
    With only AUTH_SECRET set, every other variable should fall back
    to the documented default.
    """

    def setUp(self):
        self._patch = patch.dict(os.environ, _env(), clear=True)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_returns_a_config_object(self):
        cfg = load_config()
        self.assertIsNotNone(cfg)

    def test_default_host_is_all_interfaces(self):
        cfg = load_config()
        self.assertEqual(cfg.host, "0.0.0.0")

    def test_default_port_is_8000(self):
        cfg = load_config()
        self.assertEqual(cfg.port, 8000)

    def test_default_log_level_is_info(self):
        cfg = load_config()
        self.assertEqual(cfg.log_level, "INFO")

    def test_default_relay_name(self):
        cfg = load_config()
        self.assertEqual(cfg.relay_name, "py_relay")

    def test_default_heartbeat_interval(self):
        cfg = load_config()
        self.assertEqual(cfg.heartbeat_interval, 25)

    def test_default_auth_timeout(self):
        cfg = load_config()
        self.assertEqual(cfg.auth_timeout, 15)

    def test_default_client_timeout(self):
        cfg = load_config()
        self.assertEqual(cfg.client_timeout, 90)

    def test_default_max_clients(self):
        cfg = load_config()
        self.assertEqual(cfg.max_clients, 100)

    def test_default_max_message_size_is_64_kib(self):
        cfg = load_config()
        self.assertEqual(cfg.max_message_size, 64 * 1024)

    def test_protocol_version_is_1(self):
        cfg = load_config()
        self.assertEqual(cfg.protocol_version, 1)

    def test_auth_secret_is_stored(self):
        cfg = load_config()
        self.assertEqual(cfg.auth_secret, "test-secret-for-unit-tests")


# ---------------------------------------------------------------------------
# Custom env values
# ---------------------------------------------------------------------------

class TestConfigCustomValues(unittest.TestCase):
    """
    Custom environment variables must override the defaults.
    """

    def _load(self, **extra):
        with patch.dict(os.environ, _env(**extra), clear=True):
            return load_config()

    def test_custom_port(self):
        cfg = self._load(PORT="9001")
        self.assertEqual(cfg.port, 9001)

    def test_custom_host(self):
        cfg = self._load(HOST="127.0.0.1")
        self.assertEqual(cfg.host, "127.0.0.1")

    def test_custom_log_level_lower_case_is_uppercased(self):
        cfg = self._load(LOG_LEVEL="debug")
        self.assertEqual(cfg.log_level, "DEBUG")

    def test_custom_relay_name(self):
        cfg = self._load(RELAY_NAME="my-relay")
        self.assertEqual(cfg.relay_name, "my-relay")

    def test_custom_heartbeat_interval(self):
        cfg = self._load(HEARTBEAT_INTERVAL="30")
        self.assertEqual(cfg.heartbeat_interval, 30)

    def test_custom_auth_timeout(self):
        cfg = self._load(AUTH_TIMEOUT="20")
        self.assertEqual(cfg.auth_timeout, 20)

    def test_custom_client_timeout(self):
        cfg = self._load(CLIENT_TIMEOUT="120")
        self.assertEqual(cfg.client_timeout, 120)

    def test_custom_max_clients(self):
        cfg = self._load(MAX_CLIENTS="50")
        self.assertEqual(cfg.max_clients, 50)

    def test_custom_max_message_size(self):
        cfg = self._load(MAX_MESSAGE_SIZE="4096")
        self.assertEqual(cfg.max_message_size, 4096)

    def test_auth_secret_value_is_used(self):
        cfg = self._load(AUTH_SECRET="my-specific-secret")
        self.assertEqual(cfg.auth_secret, "my-specific-secret")


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------

class TestConfigImmutable(unittest.TestCase):

    def test_config_is_frozen(self):
        """
        Config is a frozen dataclass; attribute assignment must raise.
        """
        with patch.dict(os.environ, _env(), clear=True):
            cfg = load_config()
        with self.assertRaises((AttributeError, TypeError)):
            cfg.host = "10.0.0.1"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Secret log safety
# ---------------------------------------------------------------------------

class TestSecretNotInLogs(unittest.TestCase):
    """
    AUTH_SECRET must never appear in log output from any part of the system.
    """

    def _capture_logs(self, level=logging.DEBUG):
        """Return a StringIO log handler attached to the root logger."""
        handler = logging.StreamHandler(StringIO())
        handler.setLevel(level)
        root = logging.getLogger()
        root.addHandler(handler)
        old_level = root.level
        root.setLevel(level)
        return handler, old_level

    def _remove_handler(self, handler, old_level):
        root = logging.getLogger()
        root.removeHandler(handler)
        root.setLevel(old_level)

    def test_secret_not_logged_on_verify_credential_failure(self):
        """
        verify_credential must not write the secret to any log on failure.
        """
        from auth import verify_credential

        secret = "CANARY_SECRET_MUST_NOT_APPEAR_IN_LOGS"
        handler, old_level = self._capture_logs()
        try:
            verify_credential("nonce", "client", "wrong_credential", secret)
        finally:
            self._remove_handler(handler, old_level)

        log_output = handler.stream.getvalue()
        self.assertNotIn(secret, log_output)

    def test_secret_not_logged_on_verify_credential_success(self):
        """
        verify_credential must not write the secret to any log on success either.
        """
        from auth import compute_credential, verify_credential

        secret = "CANARY_SECRET_MUST_NOT_APPEAR_IN_LOGS"
        cred = compute_credential("n", "c", secret)
        handler, old_level = self._capture_logs()
        try:
            verify_credential("n", "c", cred, secret)
        finally:
            self._remove_handler(handler, old_level)

        log_output = handler.stream.getvalue()
        self.assertNotIn(secret, log_output)

    def test_secret_not_logged_on_compute_credential(self):
        """
        compute_credential must not write the secret to any log.
        """
        from auth import compute_credential

        secret = "CANARY_SECRET_MUST_NOT_APPEAR_IN_LOGS"
        handler, old_level = self._capture_logs()
        try:
            compute_credential("nonce", "client", secret)
        finally:
            self._remove_handler(handler, old_level)

        log_output = handler.stream.getvalue()
        self.assertNotIn(secret, log_output)


if __name__ == "__main__":
    unittest.main()
