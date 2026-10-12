"""Loopback-only HTTP API for Alice Board with explicit owner Bearer authorization."""

from __future__ import annotations

import json
import os
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .store import BoardConflict, BoardError, BoardStore, BoardUnavailable

ASSETS = Path(__file__).with_name("static")
MAX_BODY = 16 * 1024
THREAD_ROUTE = re.compile(r"/api/threads/(thread-[0-9]{6,})")
POST_ROUTE = re.compile(r"/api/threads/(thread-[0-9]{6,})/posts")
TASK_ROUTE = re.compile(r"/api/tasks/(task-[0-9]{6,})")
STATUS_ROUTE = re.compile(r"/api/tasks/(task-[0-9]{6,})/status")
EVIDENCE_ROUTE = re.compile(r"/api/tasks/(task-[0-9]{6,})/evidence")


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate json key")
        obj[key] = value
    return obj


def _fields(data: dict[str, Any], *, allowed: set[str], required: set[str]) -> None:
    if set(data) - allowed or required - set(data):
        raise BoardError("unknown or missing fields")


def create_server(
    store: BoardStore, token: str, *, host: str = "127.0.0.1", port: int = 0
) -> ThreadingHTTPServer:
    """Create a single-process board API; never expose it to a network interface."""
    if host != "127.0.0.1":
        raise ValueError("Alice Board must bind to loopback behind a trusted proxy")
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("ALICE_BOARD_TOKEN must be a high-entropy secret of 32+ characters")

    class Handler(BaseHTTPRequestHandler):
        server_version = "AliceBoard/0.1"
        sys_version = ""

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, _format: str, *_args: Any) -> None:
            # Neither request paths nor Authorization headers belong in logs.
            return

        def _send(self, code: int, data: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self'; base-uri 'none'; "
                "form-action 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            self._send(code, body, "application/json; charset=utf-8")

        def _auth(self) -> bool:
            provided = self.headers.get("Authorization", "")
            if not secrets.compare_digest(provided, "Bearer " + token):
                self._json(401, {"error": "authentication_required"})
                return False
            return True

        def _request(self) -> dict[str, Any]:
            mime = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if mime != "application/json" or self.headers.get("Transfer-Encoding"):
                raise BoardError("Content-Type must be application/json")
            length = self.headers.get("Content-Length", "")
            if len(length) > 8 or not length.isdecimal() or not 0 < int(length) <= MAX_BODY:
                raise BoardError("invalid request length")
            body = self.rfile.read(int(length))
            try:
                data = json.loads(body, object_pairs_hook=_unique_pairs)
            except (ValueError, UnicodeError) as exc:
                raise BoardError("invalid JSON") from exc
            if not isinstance(data, dict):
                raise BoardError("request body must be an object")
            return data

        def _handle(self, work: Any) -> None:
            try:
                work()
            except BoardConflict as exc:
                self._json(409, {"error": str(exc)})
            except BoardError as exc:
                code = 404 if "not found" in str(exc) else 400
                self._json(code, {"error": str(exc)})
            except BoardUnavailable:
                self._json(503, {"error": "storage_unavailable"})
            except (OSError, RuntimeError, TypeError, KeyError):
                self._json(500, {"error": "internal_error"})

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/healthz":
                healthy = store.healthy()
                return self._json(
                    200 if healthy else 503,
                    {"ok": healthy, "storage": "file-native Memory DB"},
                )
            asset_names = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/static/app.css": ("app.css", "text/css; charset=utf-8"),
            }
            if path in asset_names:
                name, mime = asset_names[path]
                return self._handle(lambda: self._send(200, (ASSETS / name).read_bytes(), mime))
            if not self._auth():
                return

            def handle() -> None:
                if path == "/api/threads":
                    return self._json(200, {"threads": store.threads()})
                if match := THREAD_ROUTE.fullmatch(path):
                    return self._json(200, store.thread(match.group(1)))
                if path == "/api/tasks":
                    return self._json(200, {"tasks": store.tasks()})
                if match := TASK_ROUTE.fullmatch(path):
                    return self._json(200, store.task(match.group(1)))
                if path == "/api/export":
                    return self._json(200, store.export())
                self._json(404, {"error": "not_found"})

            return self._handle(handle)

        def do_POST(self) -> None:
            if not self._auth():
                return
            path = urlsplit(self.path).path

            def handle() -> None:
                data = self._request()
                request_id = data.get("request_id")
                if not isinstance(request_id, str):
                    raise BoardError("idempotency key must be a string")
                if path == "/api/threads":
                    _fields(
                        data,
                        allowed={"request_id", "title", "body", "category"},
                        required={"request_id", "title", "body", "category"},
                    )
                    created = store.create_thread(
                        data["title"], data["body"], data["category"], request_id
                    )
                    return self._json(201, created)
                if match := POST_ROUTE.fullmatch(path):
                    _fields(
                        data,
                        allowed={"request_id", "body"},
                        required={"request_id", "body"},
                    )
                    post = store.post(match.group(1), data["body"], request_id)
                    return self._json(201, post)
                if path == "/api/tasks":
                    _fields(
                        data,
                        allowed={"request_id", "title", "depends_on"},
                        required={"request_id", "title", "depends_on"},
                    )
                    task = store.create_task(data["title"], data["depends_on"], request_id)
                    return self._json(201, task)
                if match := STATUS_ROUTE.fullmatch(path):
                    _fields(
                        data,
                        allowed={"request_id", "status", "accepted_by_owner"},
                        required={"request_id", "status", "accepted_by_owner"},
                    )
                    task = store.set_status(
                        match.group(1), data["status"], data["accepted_by_owner"], request_id
                    )
                    return self._json(200, task)
                if match := EVIDENCE_ROUTE.fullmatch(path):
                    _fields(
                        data,
                        allowed={"request_id", "summary", "url"},
                        required={"request_id", "summary", "url"},
                    )
                    task = store.add_evidence(
                        match.group(1), data["summary"], data["url"], request_id
                    )
                    return self._json(201, task)
                self._json(404, {"error": "not_found"})

            return self._handle(handle)

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    """Start only when a dedicated token and mounted persistent data root are configured."""
    token = os.environ.get("ALICE_BOARD_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("ALICE_BOARD_TOKEN (32+ characters) is required")
    path = os.environ.get("ALICE_BOARD_DB", "/data/alice-board/board.memory")
    port = int(os.environ.get("ALICE_BOARD_PORT", "8787"))
    with BoardStore(path) as store:
        server = create_server(store, token, port=port)
        try:
            print(f"Alice Board listening on 127.0.0.1:{port}", flush=True)
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
