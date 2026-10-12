# Fallback control server (alice-lan-c17)

Self-hosted command queue for controlling Alice Pro and the Redmi 9 when the
paid RDC, a vendor tunnel or an external control service is unavailable
(#1178). One C17 source file, no dependencies beyond libc, no API keys.

Status: server side only. The phone agent that polls this server, the TLS
ingress configuration and real Redmi 9 end-to-end evidence are separate steps
of #1178. Nothing here touches the running RDC.

## Build and test

```bash
bash scripts/build_c17_server.sh           # build/alice-lan-c17 (gcc -O2)
bash scripts/build_c17_server.sh --check   # + gcc/clang ASan/UBSan builds, each run against the tests
```

Tests: `tests/test_alice_c17_tunnel_red.py`, `tests/test_alice_c17_tunnel_extended_red.py`
(black-box contract) and `tests/test_alice_c17_server_safety.py` (startup and
journal recovery). CI runs `--check` in the Application tests job.

## Run

```bash
install -d -m 700 /var/lib/alice-fallback
# 0600 files, printable ASCII, at least 16 bytes, admin and agent must differ
alice-lan-c17 --listen 127.0.0.1:8790 \
  --state-dir /var/lib/alice-fallback \
  --ready-file /run/alice-fallback/ready.json \
  --admin-token-file /etc/alice-fallback/admin.token \
  --agent-token-file /etc/alice-fallback/agent.token
```

The listener accepts only `127.0.0.1`. HTTPS is terminated by an
owner-controlled reverse proxy in front of it. When the socket is bound, the
server writes `{"url":"http://127.0.0.1:<port>"}` to the ready file
atomically; a graceful stop (SIGTERM/SIGINT) removes it.

Startup refuses (non-zero exit, no ready file) when the listener is not
loopback, a token file is readable by group/others or too short, both tokens
are equal, the state directory is not `0700` and owned by the user, another
server holds the store lock, or the journal is corrupt.

## API

All requests need `Authorization: Bearer <token>`. Missing or wrong token: 401.
Right token, wrong role: 403.

| Request | Role | Result |
| --- | --- | --- |
| `POST /v1/commands` `{"id","kind","payload":{}}` | admin | 201; 409 duplicate id; 422 bad id/kind/shape; 429 more than 1024 unfinished commands |
| `GET /v1/commands/poll?timeout_ms=N` (0..30000) | agent | 200 `{"commands":[...]}` (up to 32) or 204 after the timeout |
| `POST /v1/commands/{id}/ack` `{"status":"done"\|"failed"}` | agent | 200; 404 unknown; 409 already acknowledged |
| `GET /v1/commands/{id}` | admin | 200 `{"id","kind","state"}`; 404 unknown |

- IDs: `[A-Za-z0-9._-]`, 1–128 characters, not `.`, `..` or `poll`. Kinds:
  `[a-z0-9._-]`, 1–64. No escapes are accepted in either.
- Bodies are strict JSON (UTF-8 checked, depth ≤ 32), at most 64 KiB, with
  `Content-Type: application/json` and `Content-Length`. Any
  `Transfer-Encoding` is rejected, which also closes CL.TE request smuggling.
- A delivered command is leased to the agent for 30 s; if it is not
  acknowledged it is delivered again. States: `queued`, `delivered`, `done`,
  `failed`. After a restart unacknowledged commands are `queued` again.
- One request per connection (`Connection: close`).

## Storage

`<state-dir>/journal` is append-only. Each create and acknowledgement is one
length-prefixed record with a CRC32 and is `fdatasync`'d before the response
is sent, so an accepted ID is never lost or reused after a crash. On start the
journal is replayed: a torn last record (crash during append, never
acknowledged) is truncated, any other damage stops the server.
`<state-dir>/lock` holds a POSIX record lock for the process lifetime.

Limits: 65,536 commands and 64 MiB of payloads per store, 256 MiB journal.
There is no compaction yet; when a limit is reached creates return 507.

## Not covered yet

- Phone agent (outbound HTTPS long-poll, ACK, reconnect with backoff).
- TLS ingress configuration and certificate trust bootstrap.
- Short-lived signed sessions instead of static bearer tokens.
- Journal compaction and backup.
- Real Redmi 9 end-to-end evidence.
