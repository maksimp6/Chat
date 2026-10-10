"""Separate, fail-closed signed-grant Unix broker for Alice Mouse.

This staging broker never opens /dev/uinput. Only the authorized Live Server
process may submit grants; each grant is independently verified before forwarding.
"""

from __future__ import annotations
import base64
import collections
import secrets
import hmac
import json
import os
import re
import stat
import socket
import struct
import threading
import time
from pathlib import Path

MAX_PACKET = 2048


class SignedBroker:
    def __init__(self, key: bytes, server_uid: int, *, clock=None, forward=None):
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError("HMAC-SHA256 requires 32-byte key")
        self.key = key
        self.server_uid = server_uid
        self.clock = clock or time.monotonic_ns
        self.forward = forward or (lambda command: False)
        self.seen = collections.OrderedDict()
        self.epoch = secrets.token_hex(16)
        self.lock = threading.Lock()
        self.last_sequence = 0

    def verify(self, peer_uid: int, packet: bytes):
        if peer_uid != self.server_uid or len(packet) > MAX_PACKET:
            return None
        try:
            outer = json.loads(packet)
            raw = base64.b64decode(outer["grant"], validate=True)
            signature = bytes.fromhex(outer["sig"])
            if len(raw) > 512 or len(signature) != 32:
                return None
            if not hmac.compare_digest(hmac.digest(self.key, raw, "sha256"), signature):
                return None
            grant = json.loads(raw)
            if set(grant) != {
                "role",
                "action",
                "seq",
                "nonce",
                "until",
                "epoch",
                "x",
                "y",
                "button",
            }:
                return None
            if grant["epoch"] != self.epoch:
                return None
            if type(grant["x"]) is not int or type(grant["y"]) is not int:
                return None
            if not -500 <= grant["x"] <= 500 or not -500 <= grant["y"] <= 500:
                return None
            if type(grant["button"]) is not int or grant["button"] not in (1, 2):
                return None
            if grant["role"] not in ("controller", "admin"):
                return None
            if grant["action"] not in ("move", "down", "up"):
                return None
            if type(grant["seq"]) is not int or grant["seq"] < 1:
                return None
            if (
                type(grant["until"]) is not int
                or not 0 <= grant["until"] - self.clock() <= 300_000_000
            ):
                return None
            nonce = grant["nonce"]
            if not isinstance(nonce, str) or re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
                return None
            with self.lock:
                now = self.clock()
                for n, until in list(self.seen.items()):
                    if until < now:
                        self.seen.pop(n, None)
                if nonce in self.seen or grant["seq"] <= self.last_sequence:
                    return None
                if len(self.seen) >= 1024:
                    return None
                self.seen[nonce] = grant["until"]
                self.last_sequence = grant["seq"]
            return grant
        except (ValueError, TypeError, KeyError, UnicodeError):
            return None

    def rotate(self, new_key):
        if len(new_key) < 32:
            raise ValueError("weak key")
        with self.lock:
            self.key = new_key
            self.epoch = secrets.token_hex(16)
            self.seen.clear()
            self.last_sequence = 0

    def handle(self, peer_uid, packet):
        grant = self.verify(peer_uid, packet)
        if grant is None:
            return b"DENIED\n"
        return b"OK\n" if self.forward(grant) else b"DEVICE_UNAVAILABLE\n"


def serve(path: Path, broker: SignedBroker, stop: threading.Event, ready: threading.Event):
    parent = path.parent.stat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise PermissionError("socket directory must be private and owned by daemon")
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.settimeout(0.1)
        server.bind(str(path))
        os.chmod(path, 0o600)
        server.listen(8)
        ready.set()
        try:
            while not stop.is_set():
                try:
                    peer, _ = server.accept()
                except socket.timeout:
                    continue
                with peer:
                    peer.settimeout(0.2)
                    try:
                        pid, uid, gid = struct.unpack(
                            "3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                        )
                        payload = peer.recv(MAX_PACKET + 1)
                        peer.sendall(broker.handle(uid, payload))
                    except (OSError, TimeoutError):
                        pass
        finally:
            path.unlink(missing_ok=True)
