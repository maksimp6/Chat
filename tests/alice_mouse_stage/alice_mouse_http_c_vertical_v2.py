"""TEST-ONLY authenticated HTTP -> persistent C verifier -> event-print sink.

This never touches the installed Live Server, root broker or /dev/uinput.
The private parent-child pipe is not a public session issuance endpoint.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from alice_mouse_live_role_adapter import LiveRoleAuthority

GRANT_FORMAT = "<IIIQBBBBhhQ16s"
GRANT_MAGIC = 0x414D5331
BINARY = Path(__file__).with_name("alice_mouse_c_vertical_v1")


class CVerifier:
    def __init__(self):
        self.key = secrets.token_bytes(32)
        read_fd, write_fd = os.pipe()
        os.write(write_fd, self.key)
        os.close(write_fd)
        try:
            self.process = subprocess.Popen(
                [str(BINARY)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                pass_fds=(read_fd,),
                env={**os.environ, "ALICE_TEST_KEY_FD": str(read_fd)},
                cwd=Path(__file__).parent,
            )
        finally:
            os.close(read_fd)
        self.lock = threading.Lock()
        line = self.process.stdout.readline().strip()
        if not line.startswith("READY "):
            raise RuntimeError("C verifier failed startup")
        self.epoch = int(line.split()[1])
        self.session_id = None

    def call(self, command: str) -> str:
        with self.lock:
            if self.process.poll() is not None:
                raise RuntimeError("C verifier exited")
            self.process.stdin.write(command + "\n")
            self.process.stdin.flush()
            return self.process.stdout.readline().strip()

    def issue_after_controller_auth(self) -> bool:
        response = self.call("ISSUE")
        if not response.startswith("SESSION "):
            return False
        self.session_id = int(response.split()[1])
        return bool(self.session_id)

    def build_grant(self, action: int, seq: int, x: int, y: int, button: int) -> str:
        if self.session_id is None:
            raise PermissionError("missing authenticated session")
        issued = time.monotonic_ns() // 1_000_000
        raw = struct.pack(
            GRANT_FORMAT, GRANT_MAGIC, self.epoch, seq, self.session_id,
            2, action, button, 0, x, y, issued, secrets.token_bytes(16)
        )
        return (raw + hmac.digest(self.key, raw, "sha256")).hex()

    def close(self):
        if self.process.poll() is None:
            self.call("QUIT")
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)
        self.process.stdin.close()
        self.process.stdout.close()
        self.process.stderr.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, result):
        payload = json.dumps({"result": result}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        size = self.headers.get("Content-Length", "")
        if not size.isdecimal() or not 0 < int(size) <= 512:
            return self.reply(400, "invalid_length")
        try:
            data = json.loads(self.rfile.read(int(size)))
        except (ValueError, UnicodeError):
            return self.reply(400, "invalid_json")
        authorization = self.headers.get("Authorization", "")
        if not isinstance(data, dict) or not self.server.roles.authorize_command(authorization, data):
            return self.reply(403, "denied")
        if self.path == "/mouse/start":
            if data:
                return self.reply(400, "invalid_fields")
            ok = self.server.c.issue_after_controller_auth()
            return self.reply(200 if ok else 503, "session_started" if ok else "unavailable")
        if self.path != "/mouse/command":
            return self.reply(404, "not_found")
        if set(data) != {"action", "seq", "x", "y", "button"}:
            return self.reply(400, "invalid_fields")
        if any(type(data[k]) is not int for k in data):
            return self.reply(400, "invalid_type")
        action, seq, x, y, button = (data[k] for k in ("action", "seq", "x", "y", "button"))
        if action not in (1, 2, 3) or seq < 1 or button not in (1, 2) or not (-500 <= x <= 500 and -500 <= y <= 500):
            return self.reply(400, "invalid_value")
        if self.server.c.session_id is None:
            return self.reply(403, "session_not_started")
        try:
            grant = self.server.c.build_grant(action, seq, x, y, button)
            result = self.server.c.call("VERIFY " + grant)
        except (OSError, RuntimeError, ValueError):
            return self.reply(503, "verifier_unavailable")
        return self.reply(200 if result.startswith("EVENT ") else 403, result)


class Server(ThreadingHTTPServer):
    def __init__(self, legacy_token: str, controller_token_path: Path, c: CVerifier):
        super().__init__(("127.0.0.1", 0), Handler)
        self.roles = LiveRoleAuthority(legacy_token, controller_token_path)
        self.c = c
