"""Persistent, read-only TCP session for Bambu A1 mini.

Connection setup is excluded from request RTT. One in-flight request at a
time; any I/O or protocol failure invalidates the socket (no silent retry).
"""

from __future__ import annotations

import json
import socket
import struct
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
    ) -> None:
        self.host = host
        self.timeout = timeout
        self._connector = connector
        self._sock: socket.socket | None = None
        self._sequence = 0

    def __enter__(self) -> "PrinterSession":
        if self._sock is not None:
            raise RuntimeError("session already open")
        self._sock = self._connector(self.host, 3000, timeout=self.timeout)
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            sock.close()

    def detect(self) -> tuple[dict[str, str], float]:
        """Return sanitized detect fields and request-only RTT in milliseconds."""
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
            response = decode_probe(header + _recv_exact(sock, length - 4))
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
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
