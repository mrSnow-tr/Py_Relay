"""
auth.py — Authentication logic for py_relay.

Authentication model
--------------------
py_relay uses a challenge-response HMAC scheme so the shared secret is
never transmitted over the wire:

    1. Server sends HELLO  { nonce: "<64-char hex>" }
    2. Client sends AUTH   { client_id: "...",
                             credential: HMAC-SHA256(AUTH_SECRET, "{nonce}:{client_id}") }
    3. Server recomputes the expected credential and compares with
       hmac.compare_digest() (constant-time, timing-attack safe).

The AUTH_SECRET is only ever present in the server process environment
and the client configuration.  It is never logged, serialised, or put
on the wire.

client_id format
----------------
1–64 ASCII characters: letters, digits, hyphen, underscore, dot.
"""
import hashlib
import hmac
import logging
import re
import secrets
from typing import Tuple

logger = logging.getLogger(__name__)

# Compiled once at import time.
_CLIENT_ID_RE = re.compile(r'^[a-zA-Z0-9_\-.]+$')
_MAX_CLIENT_ID_LEN = 64


# ---------------------------------------------------------------------------
# Nonce
# ---------------------------------------------------------------------------

def generate_nonce() -> str:
    """
    Return a 64-character cryptographically secure hex nonce.

    Fresh per connection; prevents credential replay across sessions.
    """
    return secrets.token_hex(32)  # 32 bytes → 64 hex chars


# ---------------------------------------------------------------------------
# Credential computation
# ---------------------------------------------------------------------------

def compute_credential(nonce: str, client_id: str, secret: str) -> str:
    """
    Compute the expected authentication credential.

    credential = lowercase-hex( HMAC-SHA256(key=secret, msg="{nonce}:{client_id}") )

    Parameters
    ----------
    nonce:     The per-connection challenge sent in HELLO.
    client_id: The client's persistent identity string.
    secret:    The shared AUTH_SECRET (never logged).

    Returns
    -------
    Lowercase hex string of the HMAC digest.
    """
    key = secret.encode("utf-8")
    msg = f"{nonce}:{client_id}".encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Credential verification
# ---------------------------------------------------------------------------

def verify_credential(
    nonce: str,
    client_id: str,
    supplied_credential: str,
    secret: str,
) -> bool:
    """
    Verify a client-supplied credential using constant-time comparison.

    Returns True only when the credential is valid.
    Returns False on any input problem, never raises.

    hmac.compare_digest() is used to prevent timing side-channel attacks.
    Do NOT substitute == for this comparison.
    """
    if not isinstance(supplied_credential, str) or not supplied_credential:
        return False
    if not isinstance(client_id, str) or not client_id:
        return False
    if not isinstance(nonce, str) or not nonce:
        return False

    try:
        expected = compute_credential(nonce, client_id, secret)
        return hmac.compare_digest(expected, supplied_credential)
    except Exception:
        # Absorb unexpected errors — never let an exception leak credential info.
        logger.debug("verify_credential raised unexpectedly; returning False.")
        return False


# ---------------------------------------------------------------------------
# client_id validation
# ---------------------------------------------------------------------------

def validate_client_id(client_id: object) -> Tuple[bool, str]:
    """
    Validate the format of a client_id string.

    Rules
    -----
    - Must be a non-empty str
    - At most 64 characters
    - Only ASCII letters, digits, hyphen, underscore, and dot

    Returns
    -------
    (True, "")            if valid
    (False, reason_str)   if invalid
    """
    if not isinstance(client_id, str):
        return False, "client_id must be a string"
    if not client_id:
        return False, "client_id must not be empty"
    if len(client_id) > _MAX_CLIENT_ID_LEN:
        return False, f"client_id must be {_MAX_CLIENT_ID_LEN} characters or fewer"
    if not _CLIENT_ID_RE.match(client_id):
        return False, "client_id contains invalid characters (allowed: a-z A-Z 0-9 _ - .)"
    return True, ""
