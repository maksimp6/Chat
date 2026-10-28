"""Dedicated Memory DB Unix-socket service. Run under a private service UID.

No shell, path or filesystem operations are exposed to clients. Each request
is one committed operation. The caller must be a different, explicitly allowed
UID; untrusted code must never execute under the service UID.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import struct
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from memory_engine import MemoryStore, StoreError

logger = logging.getLogger("logos.memory_engine.service")

MAX_REQUEST = 65536
MAX_RESPONSE = 65536
MAX_REQUESTS_PER_SECOND = 20
MAX_INFLIGHT = 4


def peer_uid(conn: socket.socket) -> int:
    if not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeError("Linux SO_PEERCRED required")
    raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    _pid, uid, _gid = struct.unpack("3i", raw)
    return int(uid)


def receive(conn: socket.socket) -> dict[str, object]:
    data = bytearray()
    # The socket timeout bounds the whole request, not each recv: a peer that
    # trickles bytes must not hold a worker slot past one deadline.
    timeout = conn.gettimeout()
    deadline = time.monotonic() + timeout if timeout else None
    try:
        while len(data) <= MAX_REQUEST:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("request deadline exceeded")
                conn.settimeout(remaining)
            part = conn.recv(min(4096, MAX_REQUEST + 1 - len(data)))
            if not part:
                break
            data.extend(part)
            if b"\n" in part:
                break
    finally:
        conn.settimeout(timeout)
    if len(data) > MAX_REQUEST or not data.endswith(b"\n"):
        raise ValueError("invalid request length")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("invalid request")
    return value


def _operate(store: MemoryStore, request: dict[str, object]) -> dict[str, object]:
    """Run one validated operation; a rejected write is answered, not dropped."""
    op = request.get("op")
    key = request.get("key")
    if op == "info":
        return {"ok": True, "sequence": store.info().last_commit}
    if op not in {"get", "set"} or not isinstance(key, str) or not key:
        return {"ok": False, "error": "invalid operation"}
    try:
        if op == "get":
            return {"ok": True, "value": store.get(key)}
        with store._engine._lock:
            store.set(key, request.get("value"))
            return {"ok": True, "sequence": store.commit()}
    except StoreError:
        return {"ok": False, "error": "rejected"}


def handle_client(conn: socket.socket, store: MemoryStore, allowed_uid: int) -> None:
    """Bound each peer to one request and a short receive/send deadline."""
    conn.settimeout(2.0)
    if peer_uid(conn) != allowed_uid:
        return
    try:
        result = _operate(store, receive(conn))
        response = (
            json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode()
        if len(response) > MAX_RESPONSE:
            fallback = {"ok": False, "error": "response_too_large"}
            response = (json.dumps(fallback, separators=(",", ":")) + "\n").encode()
        conn.sendall(response)
    except (ValueError, TypeError, KeyError) as exc:
        # A malformed request is answered and logged, not dropped silently.
        logger.warning("memory request rejected: %s: %s", type(exc).__name__, exc)
        rejected = {"ok": False, "error": "invalid request"}
        try:
            conn.sendall((json.dumps(rejected, separators=(",", ":")) + "\n").encode())
        except OSError as send_exc:
            logger.warning("memory request reply failed: %s", send_exc)
    except (OSError, TimeoutError) as exc:
        logger.warning("memory connection failed: %s: %s", type(exc).__name__, exc)


def _serve_one(
    conn: socket.socket,
    store: MemoryStore,
    allowed_uid: int,
    slots: threading.BoundedSemaphore,
) -> None:
    try:
        with conn:
            handle_client(conn, store, allowed_uid)
    finally:
        slots.release()


def serve(database: Path, socket_path: Path, allowed_uid: int) -> None:
    if os.geteuid() == 0 or allowed_uid == os.geteuid():
        raise RuntimeError("service requires distinct non-root service and client UIDs")
    if os.environ.get("LOGOS_MEMORY_TRUSTED_ROOT") != "1":
        raise RuntimeError("trusted mode must be enabled before service startup")
    if socket_path.exists() or socket_path.is_symlink():
        raise RuntimeError("socket path already exists")
    with MemoryStore(database, trusted=True) as store:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # Peer credentials authorize requests, DAC permits client connections.
            # The umask makes bind create the socket as 0o666 directly, so it never
            # exists with default rights and no later chmod follows the path.
            previous_umask = os.umask(0o111)
            try:
                listener.bind(str(socket_path))
            finally:
                os.umask(previous_umask)
            listener.listen(8)
            recent: deque[float] = deque()
            slots = threading.BoundedSemaphore(MAX_INFLIGHT)
            with ThreadPoolExecutor(max_workers=MAX_INFLIGHT) as workers:
                while True:
                    conn, _ = listener.accept()
                    try:
                        uid = peer_uid(conn)
                    except OSError as exc:
                        # A peer that vanished before the credential check only
                        # drops its own connection, never the whole service.
                        logger.warning("memory peer check failed: %s", exc)
                        conn.close()
                        continue
                    if uid != allowed_uid:
                        conn.close()
                        continue
                    now = time.monotonic()
                    while recent and now - recent[0] >= 1.0:
                        recent.popleft()
                    if len(recent) >= MAX_REQUESTS_PER_SECOND or not slots.acquire(
                        blocking=False
                    ):
                        conn.close()
                        continue
                    recent.append(now)
                    try:
                        workers.submit(_serve_one, conn, store, allowed_uid, slots)
                    except RuntimeError:
                        slots.release()
                        conn.close()
                        raise
        finally:
            listener.close()
            socket_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--allowed-uid", type=int, required=True)
    args = parser.parse_args()
    serve(args.database, args.socket, args.allowed_uid)


if __name__ == "__main__":
    main()
