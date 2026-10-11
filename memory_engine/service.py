"""Dedicated Memory DB Unix-socket service. Run under a private service UID.

No shell, path or filesystem operations are exposed to clients. Each request
is one committed operation. The caller must be a different, explicitly allowed
UID; untrusted code must never execute under the service UID.
"""

from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import struct
import threading
import time

from memory_engine import MemoryStore

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
    while len(data) <= MAX_REQUEST:
        part = conn.recv(min(4096, MAX_REQUEST + 1 - len(data)))
        if not part:
            break
        data.extend(part)
        if b"\n" in part:
            break
    if len(data) > MAX_REQUEST or not data.endswith(b"\n"):
        raise ValueError("invalid request length")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("invalid request")
    return value


def handle_client(conn: socket.socket, store: MemoryStore, allowed_uid: int) -> None:
    """Bound each peer to one request and a short receive/send deadline."""
    conn.settimeout(2.0)
    if peer_uid(conn) != allowed_uid:
        return
    try:
        request = receive(conn)
        op = request.get("op")
        key = request.get("key")
        if op == "get" and isinstance(key, str):
            result = {"ok": True, "value": store.get(key)}
        elif op == "set" and isinstance(key, str):
            store.set(key, request.get("value"))
            result = {"ok": True, "sequence": store.commit()}
        elif op == "info":
            result = {"ok": True, "sequence": store.info().last_commit}
        else:
            result = {"ok": False, "error": "invalid operation"}
        response = (json.dumps(result, separators=(",", ":")) + "\n").encode()
        if len(response) > MAX_RESPONSE:
            return
        conn.sendall(response)
    except (ValueError, TypeError, KeyError, OSError, TimeoutError):
        return


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
    if os.environ.get("ALICE_MEMORY_TRUSTED_ROOT") != "1":
        raise RuntimeError("trusted mode must be enabled before service startup")
    if socket_path.exists() or socket_path.is_symlink():
        raise RuntimeError("socket path already exists")
    with MemoryStore(database, trusted=True) as store:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(socket_path))
            # Peer credentials authorize requests, DAC permits client connections.
            os.chmod(socket_path, 0o666)
            listener.listen(8)
            recent: deque[float] = deque()
            slots = threading.BoundedSemaphore(MAX_INFLIGHT)
            with ThreadPoolExecutor(max_workers=MAX_INFLIGHT) as workers:
                while True:
                    conn, _ = listener.accept()
                    if peer_uid(conn) != allowed_uid:
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
