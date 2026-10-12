"""Persistent, read-only TCP session for Bambu A1 mini.

Connection setup is excluded from request RTT. One in-flight request at a
time; any I/O or protocol failure invalidates the socket (no silent retry).
"""

from __future__ import annotations

import json
import socket
import struct
import threading
import time
from typing import Callable

from .bambu_a1mini import PrinterProtocolError, decode_probe, _recv_exact
from .lan_socket import connect_lan


class PrinterSession:
    def __init__(
        self,
        host: str,
        *,
        timeout: float = 3.0,
        connector: Callable[..., socket.socket] = connect_lan,
        tcp_nodelay: bool = False,
        keepalive: bool = False,
    ) -> None:
        self.host = host
        self.timeout = timeout
        self._connector = connector
        self._sock: socket.socket | None = None
        self._sequence = 0
        self._lock = threading.RLock()
        self.tcp_nodelay = tcp_nodelay
        self.keepalive = keepalive

    def __enter__(self) -> "PrinterSession":
        with self._lock:
            if self._sock is not None:
                raise RuntimeError("session already open")
            sock = self._connector(self.host, 3000, timeout=self.timeout)
            try:
                if self.tcp_nodelay:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                if self.keepalive:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except BaseException:
                sock.close()
                raise
            self._sock = sock
            return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            sock, self._sock = self._sock, None
            if sock is not None:
                sock.close()

    def detect(self) -> tuple[dict[str, str], float]:
        """Serialize requests; return RTT ending at final recv, before JSON decode."""
        with self._lock:
            return self._detect_locked()

    def _detect_locked(self) -> tuple[dict[str, str], float]:
        sock = self._sock
        if sock is None:
            raise RuntimeError("session not connected")
        self._sequence += 1
        body = json.dumps(
            {"login": {"command": "detect", "sequence_id": str(self._sequence)}},
            separators=(",", ":"),
        ).encode()
        frame = b"\xa5\xa5" + struct.pack("<H", len(body) + 6) + body + b"\xa7\xa7"
        try:
            started = time.perf_counter_ns()
            sock.sendall(frame)
            header = _recv_exact(sock, 4)
            if not header.startswith(b"\xa5\xa5"):
                raise PrinterProtocolError("invalid response header")
            length = struct.unpack("<H", header[2:4])[0]
            if not 6 <= length <= 65535:
                raise PrinterProtocolError("invalid response length")
            raw = header + _recv_exact(sock, length - 4)
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            response = decode_probe(raw)
            login = response.get("login")
            if (
                not isinstance(login, dict)
                or login.get("command") != "detect"
                or not login.get("model")
            ):
                raise PrinterProtocolError("invalid detect response")
            if str(login.get("sequence_id", str(self._sequence))) != str(self._sequence):
                raise PrinterProtocolError("sequence mismatch")
            return {
                "model": str(login["model"]),
                "version": str(login.get("version", "")),
            }, elapsed_ms
        except BaseException:
            self.close()
            raise
