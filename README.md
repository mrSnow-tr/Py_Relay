# py_relay

A lightweight, production-grade WebSocket relay and rendezvous server.

Clients behind NAT or private networks make outbound persistent WebSocket connections to py_relay. The relay authenticates them, tracks their logical identities, and provides the foundation for authenticated client-to-client communication — without requiring clients to have public IP addresses.

---

## Architecture

```
                   INTERNET  (TLS)
                       |
               +-------+--------+
               |    py_relay    |
               |  Render.com    |
               +-------+--------+
                       |  (plain WebSocket, internal)
          +------------+------------+
          |            |            |
       Client A     Client B     Client C
     (behind NAT) (behind NAT) (behind NAT)
```

py_relay is the publicly reachable rendezvous point. Clients initiate outbound connections; the relay never needs to reach into private networks.

---

## Why WebSocket

- Works through NAT, corporate firewalls, and HTTP proxies without special configuration.
- Full-duplex: the server can push messages to clients at any time (required for relay forwarding and heartbeats).
- Runs over standard HTTPS/443 ports when TLS is terminated at Render's edge.
- Persistent connections with well-defined close semantics make session tracking straightforward.

---

## TLS and Transport Security

Render terminates TLS at their edge load balancer. Public clients connect using `wss://` (WebSocket over TLS). Render forwards decrypted traffic to py_relay on `$PORT` over its internal private network.

The result:

```
Client  ──(wss:// TLS)──►  Render edge  ──(ws:// private)──►  py_relay
```

**AUTH_SECRET is never transmitted over the wire** — see Authentication below.

For a self-hosted deployment without a TLS-terminating proxy, configure a reverse proxy (nginx, Caddy) in front of py_relay to handle TLS.

---

## Environment Variables

| Variable             | Required | Default    | Description                                                   |
|----------------------|----------|------------|---------------------------------------------------------------|
| `AUTH_SECRET`        | **yes**  | —          | Shared HMAC secret. Never logged or transmitted.              |
| `PORT`               | yes (Render provides it) | `8000` | Listening port. Render sets this automatically.  |
| `HOST`               | no       | `0.0.0.0`  | Bind address.                                                 |
| `RELAY_NAME`         | no       | `py_relay` | Human-readable name in HELLO and /health responses.           |
| `PROTOCOL_VERSION`   | no       | `1`        | Protocol version enforced on every message.                   |
| `LOG_LEVEL`          | no       | `INFO`     | `DEBUG` / `INFO` / `WARNING` / `ERROR`.                       |
| `HEARTBEAT_INTERVAL` | no       | `25`       | Seconds between server→client HEARTBEAT messages.             |
| `AUTH_TIMEOUT`       | no       | `15`       | Seconds a new connection has to authenticate before eviction. |
| `CLIENT_TIMEOUT`     | no       | `90`       | Seconds of silence before a session is considered dead.       |
| `MAX_CLIENTS`        | no       | `100`      | Hard cap on simultaneously authenticated clients.             |
| `MAX_MESSAGE_SIZE`   | no       | `65536`    | Maximum WebSocket message size in bytes (64 KiB).             |

`AUTH_SECRET` is the only variable the server refuses to start without. Set it to a long, random string (32+ bytes of entropy).

Generate one:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## Render Deployment

### First deploy

1. Fork or push this repository to GitHub / GitLab.
2. In the Render dashboard, choose **New → Web Service** and connect the repository.
3. Render will detect `render.yaml` and pre-fill the service configuration.
4. Click **Create Web Service**.
5. Render generates `AUTH_SECRET` automatically (`generateValue: true` in `render.yaml`). Copy the generated value from **Environment** in the dashboard — every py_client needs it.

### Build command

```
pip install -r requirements.txt
```

### Start command

```
python app.py
```

### Health check

Render polls `GET /health` to determine liveness. The endpoint returns HTTP 200 with a JSON body and requires no authentication.

```json
{"status":"ok","relay":"py-relay","uptime":3724,"clients":2}
```

---

## Local Installation

**Requires Python 3.8 or later.**

```bash
git clone <repo>
cd py_relay
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## Local Startup

```bash
export AUTH_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export HOST=127.0.0.1
export PORT=8000
export LOG_LEVEL=DEBUG

python app.py
```

Or as a module:

```bash
python -m py_relay
```

The server prints its configuration summary and then listens. Test the health endpoint:

```bash
curl http://127.0.0.1:8000/health
```

---

## Authentication Model

py_relay uses **HMAC-SHA256 challenge-response** authentication. The shared `AUTH_SECRET` is never transmitted over the wire.

### Handshake flow

```
Client                                  Relay
  |                                       |
  |<──── HELLO { nonce: "<64-hex>" } ─────|  (relay sends a fresh nonce)
  |                                       |
  |──── AUTH { client_id,                 |
  |            credential } ─────────────►|
  |                                       |  credential =
  |                                       |  HMAC-SHA256(
  |                                       |    key  = AUTH_SECRET,
  |                                       |    msg  = "{nonce}:{client_id}"
  |                                       |  ).hexdigest()
  |                                       |
  |<──── AUTH_OK { session_id } ──────────|  (or AUTH_FAILED + close)
```

Key properties:

- `AUTH_SECRET` stays in the process environment; clients compute the HMAC locally.
- The nonce is unique per connection, preventing credential replay across sessions.
- The server uses `hmac.compare_digest()` to resist timing side-channel attacks.
- `client_id` is validated for format (1–64 ASCII alphanumeric / `-` / `_` / `.`).

---

## Protocol Overview

All messages are UTF-8 JSON with this shape:

```json
{
    "version":    1,
    "type":       "<message_type>",
    "request_id": "<optional-uuid>",
    "payload":    {}
}
```

`request_id` is optional but recommended for messages that have a response — it allows client code to correlate replies.

### V1 message types

| Type            | Direction         | Purpose                                      |
|-----------------|-------------------|----------------------------------------------|
| `hello`         | Server → Client   | Opens handshake, carries server nonce        |
| `auth`          | Client → Server   | Authentication request                       |
| `auth_ok`       | Server → Client   | Authentication accepted, carries session_id  |
| `auth_failed`   | Server → Client   | Authentication rejected (connection closed)  |
| `heartbeat`     | Either direction  | Keep-alive probe                             |
| `heartbeat_ack` | Either direction  | Heartbeat acknowledgement                    |
| `ping`          | Either direction  | Connectivity check                           |
| `pong`          | Either direction  | Ping response                                |
| `disconnect`    | Either direction  | Graceful close notification                  |
| `error`         | Server → Client   | Protocol or server error                     |

Reserved for future releases (not implemented): `session_resume`, `client_info`, `peer_list`, `connect_peer`, `data`, `route`.

---

## Client Lifecycle

```
WebSocket connect
      │
      ▼
  Receive HELLO (nonce)
      │
      ▼
  Send AUTH (client_id + credential)
      │
      ├─[failure]── Receive AUTH_FAILED ──► connection closed
      │
      ▼
  Receive AUTH_OK (session_id)
      │
      ▼
  ┌─────────────────────────┐
  │   Normal operation      │
  │   Respond to HEARTBEAT  │
  │   Send PING / PONG      │
  │   ...future DATA msgs   │
  └─────────────────────────┘
      │
      ▼
  DISCONNECT or connection close
```

### Identity terminology

| Term         | Meaning                                                              |
|--------------|----------------------------------------------------------------------|
| `client_id`  | Persistent logical identity, chosen by the client (e.g. `router-01`) |
| `session_id` | UUID assigned by the relay for this specific connection              |
| connection   | The underlying WebSocket; may be replaced on reconnect               |

---

## Reconnection Behaviour

If a client disconnects and reconnects with the same `client_id` and valid credential:

1. The relay detects an existing authenticated session for that `client_id`.
2. The old session receives a `DISCONNECT` message and its WebSocket is closed.
3. The new session is registered as the authoritative session for that `client_id`.

This prevents two connections from simultaneously claiming the same identity. Clients should implement exponential backoff before reconnecting.

**Important:** restarting py_relay loses all in-memory session state. All clients will need to reconnect and re-authenticate. This is expected behaviour in V1.

---

## Running Tests

Tests use only the Python standard library (`unittest`). No test fixtures require a running server or network access. `AUTH_SECRET` is managed internally by the config tests using `unittest.mock`.

```bash
python -m unittest discover -v tests/
```

If you have [pytest](https://pypi.org/project/pytest/) installed (optional dev tool, not a runtime dependency):

```bash
python -m pytest tests/ -v
```

Test coverage:

- `test_protocol.py` — parsing, validation, version enforcement, size limits, round-trips
- `test_auth.py` — HMAC computation, constant-time verification, client_id validation
- `test_clients.py` — registry CRUD, dual-index consistency, capacity, duplicate handling
- `test_sessions.py` — session lifecycle, authenticated-only lookup, snapshot safety
- `test_config.py` — configuration defaults, env var parsing, missing-secret exit, immutability, secret log-safety

---

## Security Considerations

- `AUTH_SECRET` must be at least 32 bytes of entropy. Generate it with `secrets.token_hex(32)`.
- The secret is validated at startup; the server refuses to start without it.
- The secret is never logged, never serialised, and never put on the wire.
- HMAC-SHA256 with a fresh per-connection nonce prevents credential replay.
- `hmac.compare_digest()` is used exclusively for credential comparison.
- Messages exceeding `MAX_MESSAGE_SIZE` are rejected before parsing.
- Connections that do not authenticate within `AUTH_TIMEOUT` seconds are closed.
- Sessions silent for longer than `CLIENT_TIMEOUT` seconds are evicted.
- Each connection is isolated; an exception in one handler cannot affect others.
- No client payload is logged; only identity fields and counters appear in logs.

---

## Current Limitations (V1)

- **In-memory state only.** Restarting the server disconnects all clients and loses all session state.
- **Single relay node.** No clustering or relay-to-relay forwarding.
- **No client-to-client data routing.** The relay authenticates and tracks clients but does not yet forward data between them.
- **No session resumption.** A reconnecting client starts a fresh session.
- **No virtual IP allocation.** Planned for a future release.

---

## Future Architecture

The relay is designed to grow incrementally. Planned additions (not implemented):

- **Client-to-client relay** — opaque binary forwarding between authenticated sessions.
- **Virtual IP allocation** — each client receives a logical address in a private range.
- **Session resumption** — clients can resume after a brief disconnect without full re-auth.
- **Peer discovery** — clients can query which peers are currently reachable.
- **Direct peer connections** — relay-assisted hole-punching for low-latency paths.
- **TUN/TAP support** — operating-system virtual network interface integration in py_client.
- **Multiple relay nodes** — relay clustering and failover.

The relay intentionally does not interpret application payloads. Its long-term role is: **authenticate, identify, and route** — nothing more.
