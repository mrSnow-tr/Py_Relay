"""
tests/test_auth.py

Tests for auth.py:
- Nonce generation
- Credential computation
- Credential verification (success, wrong secret, wrong nonce, wrong client_id)
- Constant-time comparison safety (verify_credential never raises)
- validate_client_id (valid formats, length, character rules)
"""
import hmac
import unittest

from auth import (
    compute_credential,
    generate_nonce,
    validate_client_id,
    verify_credential,
)


class TestGenerateNonce(unittest.TestCase):

    def test_returns_string(self):
        self.assertIsInstance(generate_nonce(), str)

    def test_length_is_64(self):
        # 32 bytes → 64 hex characters.
        self.assertEqual(len(generate_nonce()), 64)

    def test_hex_characters_only(self):
        nonce = generate_nonce()
        int(nonce, 16)  # raises ValueError if not valid hex

    def test_nonces_are_unique(self):
        nonces = {generate_nonce() for _ in range(200)}
        self.assertEqual(len(nonces), 200)


class TestComputeCredential(unittest.TestCase):

    def test_returns_hex_string(self):
        cred = compute_credential("nonce", "client", "secret")
        self.assertIsInstance(cred, str)
        int(cred, 16)  # valid hex

    def test_sha256_length(self):
        cred = compute_credential("nonce", "client", "secret")
        self.assertEqual(len(cred), 64)  # SHA-256 = 32 bytes = 64 hex chars

    def test_deterministic(self):
        a = compute_credential("n", "c", "s")
        b = compute_credential("n", "c", "s")
        self.assertEqual(a, b)

    def test_different_nonce_gives_different_credential(self):
        a = compute_credential("nonce1", "client", "secret")
        b = compute_credential("nonce2", "client", "secret")
        self.assertNotEqual(a, b)

    def test_different_client_id_gives_different_credential(self):
        a = compute_credential("nonce", "client_a", "secret")
        b = compute_credential("nonce", "client_b", "secret")
        self.assertNotEqual(a, b)

    def test_different_secret_gives_different_credential(self):
        a = compute_credential("nonce", "client", "secret1")
        b = compute_credential("nonce", "client", "secret2")
        self.assertNotEqual(a, b)


class TestVerifyCredential(unittest.TestCase):

    _NONCE  = "testnonce123"
    _ID     = "client_001"
    _SECRET = "super-secret-value"

    def _good_cred(self) -> str:
        return compute_credential(self._NONCE, self._ID, self._SECRET)

    def test_valid_credential_returns_true(self):
        self.assertTrue(
            verify_credential(self._NONCE, self._ID, self._good_cred(), self._SECRET)
        )

    def test_wrong_secret_returns_false(self):
        cred = self._good_cred()
        self.assertFalse(
            verify_credential(self._NONCE, self._ID, cred, "wrong-secret")
        )

    def test_wrong_nonce_returns_false(self):
        cred = self._good_cred()
        self.assertFalse(
            verify_credential("different-nonce", self._ID, cred, self._SECRET)
        )

    def test_wrong_client_id_returns_false(self):
        cred = self._good_cred()
        self.assertFalse(
            verify_credential(self._NONCE, "other_client", cred, self._SECRET)
        )

    def test_tampered_credential_returns_false(self):
        cred = self._good_cred()
        tampered = cred[:-1] + ("0" if cred[-1] != "0" else "1")
        self.assertFalse(
            verify_credential(self._NONCE, self._ID, tampered, self._SECRET)
        )

    def test_empty_credential_returns_false(self):
        self.assertFalse(
            verify_credential(self._NONCE, self._ID, "", self._SECRET)
        )

    def test_empty_client_id_returns_false(self):
        self.assertFalse(
            verify_credential(self._NONCE, "", self._good_cred(), self._SECRET)
        )

    def test_empty_nonce_returns_false(self):
        self.assertFalse(
            verify_credential("", self._ID, self._good_cred(), self._SECRET)
        )

    def test_none_credential_returns_false(self):
        self.assertFalse(
            verify_credential(self._NONCE, self._ID, None, self._SECRET)  # type: ignore
        )

    def test_integer_credential_returns_false(self):
        self.assertFalse(
            verify_credential(self._NONCE, self._ID, 12345, self._SECRET)  # type: ignore
        )

    def test_uses_compare_digest_internally(self):
        """
        Smoke-test that verify_credential's behaviour matches what
        hmac.compare_digest would expect — i.e. it does not short-circuit
        on length differences in a way that leaks timing information.
        This is a behavioural / correctness test, not a true timing test.
        """
        cred = self._good_cred()
        # A truncated credential (same prefix, wrong length) must fail.
        self.assertFalse(
            verify_credential(self._NONCE, self._ID, cred[:32], self._SECRET)
        )

    def test_never_raises_on_garbage_input(self):
        """verify_credential must absorb all exceptions and return False."""
        for bad in [None, 42, [], {}, b"bytes", object()]:
            try:
                result = verify_credential(bad, bad, bad, bad)  # type: ignore
                self.assertFalse(result)
            except Exception as exc:
                self.fail(f"verify_credential raised {exc!r} on garbage input")


class TestValidateClientId(unittest.TestCase):

    def _valid(self, client_id: object) -> bool:
        ok, _ = validate_client_id(client_id)
        return ok

    def _reason(self, client_id: object) -> str:
        _, r = validate_client_id(client_id)
        return r

    # --- valid cases ---

    def test_simple_alphanumeric(self):
        self.assertTrue(self._valid("client001"))

    def test_with_hyphen(self):
        self.assertTrue(self._valid("client-001"))

    def test_with_underscore(self):
        self.assertTrue(self._valid("client_001"))

    def test_with_dot(self):
        self.assertTrue(self._valid("client.001"))

    def test_uppercase(self):
        self.assertTrue(self._valid("ClientA"))

    def test_single_char(self):
        self.assertTrue(self._valid("x"))

    def test_exactly_64_chars(self):
        self.assertTrue(self._valid("a" * 64))

    def test_mixed_allowed_chars(self):
        self.assertTrue(self._valid("My.Client_ID-v2"))

    # --- invalid cases ---

    def test_empty_string_invalid(self):
        self.assertFalse(self._valid(""))
        self.assertIn("empty", self._reason(""))

    def test_65_chars_invalid(self):
        self.assertFalse(self._valid("a" * 65))
        self.assertIn("64", self._reason("a" * 65))

    def test_space_invalid(self):
        self.assertFalse(self._valid("client 001"))
        self.assertIn("invalid characters", self._reason("client 001"))

    def test_at_sign_invalid(self):
        self.assertFalse(self._valid("client@host"))

    def test_slash_invalid(self):
        self.assertFalse(self._valid("a/b"))

    def test_colon_invalid(self):
        self.assertFalse(self._valid("a:b"))

    def test_hash_invalid(self):
        self.assertFalse(self._valid("abc#def"))

    def test_non_string_int_invalid(self):
        self.assertFalse(self._valid(42))  # type: ignore
        self.assertIn("string", self._reason(42))  # type: ignore

    def test_non_string_none_invalid(self):
        self.assertFalse(self._valid(None))  # type: ignore

    def test_non_string_list_invalid(self):
        self.assertFalse(self._valid([]))  # type: ignore


if __name__ == "__main__":
    unittest.main()
