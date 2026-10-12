"""RED black-box acceptance tests for the self-hosted C17 tunnel (#1178).

This is an intentionally strict test contract, not a reference implementation.
A missing binary, unsupported endpoint, absent persistence, or broken auth is FAIL.
Only localhost is used; no phone, paid tunnel, external TLS or cloud resources.

Executable contract (proposed; implementation may negotiate versioning in #1178):
  ALICE_C17_SERVER_BIN (default: build/alice-lan-c17)
  binary --listen 127.0.0.1:0 --state-dir DIR --ready-file FILE
         --admin-token-file FILE --agent-token-file FILE
  ready-file contains {"url":"http://127.0.0.1:<port>"}.
  Authorization: Bearer <token> (separate admin/agent tokens).
  POST /v1/commands {"id":"...", "kind":"noop", "payload":{}} -> 201/202
  GET /v1/commands/poll?timeout_ms=N -> 200 {"commands":[...]} or 204
  POST /v1/commands/ID/ack {"status":"done"} -> 200/204
  GET /v1/commands/ID -> 200 {"id":...,"state":"done"|"queued"|...}
  POST /v1/commands/ID/ack must reject repeats, cross-role and unknown IDs.
Every accepted command ID must be stable across restart. No execution is requested.
"""

import contextlib
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.parse


ROOT = Path(__file__).resolve().parents[1]
BINARY = Path(os.environ.get("ALICE_C17_SERVER_BIN", "build/alice-lan-c17"))
if not BINARY.is_absolute():
    BINARY = ROOT / BINARY


class Transport:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.process = None
        self.base_url = None
        self.admin_token = "acceptance-admin-" + "a" * 32
        self.agent_token = "acceptance-agent-" + "b" * 32

    def start(self):
        if not BINARY.is_file() or not os.access(BINARY, os.X_OK):
            raise AssertionError(
                f"RED: C17 server executable absent or not executable: {BINARY}. "
                "Build a real #1178 server; never mock this contract."
            )
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        admin_file = self.state_dir / "admin.token"
        agent_file = self.state_dir / "agent.token"
        admin_file.write_text(self.admin_token, encoding="ascii")
        agent_file.write_text(self.agent_token, encoding="ascii")
        admin_file.chmod(0o600)
        agent_file.chmod(0o600)
        ready_file = self.state_dir / "ready.json"
        ready_file.unlink(missing_ok=True)
        self.process = subprocess.Popen(
            [
                str(BINARY), "--listen", "127.0.0.1:0",
                "--state-dir", str(self.state_dir),
                "--ready-file", str(ready_file),
                "--admin-token-file", str(admin_file),
                "--agent-token-file", str(agent_file),
            ],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError(f"RED: server exited early with {self.process.returncode}")
            if ready_file.is_file():
                info = json.loads(ready_file.read_text(encoding="utf-8"))
                url = urllib.parse.urlsplit(info["url"])
                assert url.scheme == "http", "TLS terminates at local owner-controlled ingress"
                assert url.hostname == "127.0.0.1", "C17 listener must be loopback-only"
                assert 1 <= (url.port or 0) <= 65535
                self.base_url = url
                return self
            time.sleep(0.02)
        raise AssertionError("RED: server never published readiness within 5 seconds")

    def stop(self, *, crash=False):
        if self.process is None:
            return
        proc = self.process
        self.process = None
        if proc.poll() is None:
            if crash:
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate(timeout=3)
        else:
            proc.communicate(timeout=1)

    def request(self, method, path, *, role=None, body=None, token=None, headers=None, timeout=2):
        assert self.base_url is not None
        data = None if body is None else json.dumps(body).encode("utf-8")
        h = {"Content-Type": "application/json"}
        if role is not None:
            token = self.admin_token if role == "admin" else self.agent_token
        if token is not None:
            h["Authorization"] = "Bearer " + token
        h.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.base_url.port, timeout=timeout)
        try:
            conn.request(method, path, body=data, headers=h)
            response = conn.getresponse()
            payload = response.read(131073)
            assert len(payload) <= 131072, "server returned an unbounded response"
            try:
                parsed = json.loads(payload) if payload else None
            except ValueError:
                parsed = None
            return response.status, parsed, payload
        finally:
            conn.close()

    def create(self, command_id):
        return self.request(
            "POST", "/v1/commands", role="admin",
            body={"id": command_id, "kind": "noop", "payload": {}},
        )


class C17BlackBoxContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="alice-c17-contract-")
        self.addCleanup(self.temp.cleanup)
        self.server = Transport(Path(self.temp.name) / "store")
        self.addCleanup(self.server.stop)
        self.server.start()

    def test_no_unauthenticated_command_or_poll(self):
        for method, path, body in [
            ("POST", "/v1/commands", {"id": "unauth", "kind": "noop", "payload": {}}),
            ("GET", "/v1/commands/poll?timeout_ms=0", None),
            ("POST", "/v1/commands/missing/ack", {"status": "done"}),
        ]:
            with self.subTest(method=method, path=path):
                status, _, _ = self.server.request(method, path, body=body)
                self.assertIn(status, (401, 403))

    def test_admin_agent_roles_are_not_interchangeable(self):
        status, _, _ = self.server.request(
            "POST", "/v1/commands", role="agent",
            body={"id": "nope", "kind": "noop", "payload": {}},
        )
        self.assertEqual(status, 403)
        status, _, _ = self.server.request("GET", "/v1/commands/poll?timeout_ms=0", role="admin")
        self.assertEqual(status, 403)
        status, _, _ = self.server.request(
            "POST", "/v1/commands/x/ack", role="admin", body={"status": "done"},
        )
        self.assertEqual(status, 403)

    def test_incorrect_tokens_fail_closed(self):
        for credential in ("wrong", self.server.admin_token[:-1], ""):
            with self.subTest(token_length=len(credential)):
                status, _, _ = self.server.request(
                    "POST", "/v1/commands", token=credential,
                    body={"id": "bad-token", "kind": "noop", "payload": {}},
                )
                self.assertIn(status, (401, 403))

    def test_round_trip_ack_and_authoritative_state(self):
        status, _, _ = self.server.create("command-1")
        self.assertIn(status, (201, 202))
        status, data, _ = self.server.request(
            "GET", "/v1/commands/poll?timeout_ms=50", role="agent",
        )
        self.assertEqual(status, 200)
        self.assertIsInstance(data, dict)
        self.assertEqual([c["id"] for c in data["commands"]], ["command-1"])
        status, _, _ = self.server.request(
            "POST", "/v1/commands/command-1/ack", role="agent", body={"status": "done"},
        )
        self.assertIn(status, (200, 204))
        status, data, _ = self.server.request("GET", "/v1/commands/command-1", role="admin")
        self.assertEqual(status, 200)
        self.assertEqual(data["state"], "done")

    def test_duplicate_id_and_ack_replay_rejected(self):
        self.assertIn(self.server.create("once")[0], (201, 202))
        self.assertEqual(self.server.create("once")[0], 409)
        self.assertEqual(
            self.server.request(
                "POST", "/v1/commands/once/ack", role="agent", body={"status": "done"},
            )[0] in (200, 204),
            True,
        )
        self.assertIn(
            self.server.request(
                "POST", "/v1/commands/once/ack", role="agent", body={"status": "done"},
            )[0],
            (409, 410),
        )

    def test_replay_rejected_across_hard_crash(self):
        self.assertIn(self.server.create("durable-once")[0], (201, 202))
        self.server.stop(crash=True)
        self.server.start()
        self.assertEqual(self.server.create("durable-once")[0], 409)
        status, data, _ = self.server.request("GET", "/v1/commands/durable-once", role="admin")
        self.assertEqual(status, 200)
        self.assertEqual(data["id"], "durable-once")

    def test_corrupted_or_oversized_json_is_never_accepted(self):
        for data in (b"{", b"[]", b"null", b"\xff", b"x" * 131073):
            with self.subTest(payload_len=len(data)):
                conn = http.client.HTTPConnection("127.0.0.1", self.server.base_url.port, timeout=2)
                try:
                    conn.request(
                        "POST", "/v1/commands", body=data,
                        headers={
                            "Authorization": "Bearer " + self.server.admin_token,
                            "Content-Type": "application/json",
                        },
                    )
                    response = conn.getresponse()
                    response.read()
                    self.assertIn(response.status, (400, 413, 415, 422))
                finally:
                    conn.close()
        self.assertIn(self.server.create("valid-after-bad")[0], (201, 202))

    def test_poll_deadline_is_bounded(self):
        start = time.monotonic()
        status, _, _ = self.server.request(
            "GET", "/v1/commands/poll?timeout_ms=150", role="agent", timeout=2,
        )
        elapsed = time.monotonic() - start
        self.assertIn(status, (200, 204))
        self.assertGreaterEqual(elapsed, 0.10)
        self.assertLess(elapsed, 1.0)


if __name__ == "__main__":
    unittest.main()
