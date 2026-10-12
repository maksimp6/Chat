"""Read-only Bambu Lab A1 mini discovery and telemetry normalization.

No heating, motion, file upload, or print-start commands are implemented.
"""
from __future__ import annotations

import json
import socket
import struct
from typing import Any

from .models import MODEL_CODES, PrinterIdentity, merge_report, summarize_status

_FRAME_START = b"\xa5\xa5"
_FRAME_END = b"\xa7\xa7"


class PrinterProtocolError(RuntimeError):
    """Unexpected response from a printer."""


def decode_probe(data: bytes) -> dict[str, Any]:
    """Decode framed TCP/3000 detect response without exposing identifiers."""
    if len(data) < 6 or not data.startswith(_FRAME_START):
        raise PrinterProtocolError("invalid frame prefix")
    expected = struct.unpack("<H", data[2:4])[0]
    if expected != len(data) or not data.endswith(_FRAME_END):
        raise PrinterProtocolError("invalid frame length or suffix")
    try:
        message = json.loads(data[4:-2])
    except (UnicodeError, ValueError) as exc:
        raise PrinterProtocolError("invalid JSON") from exc
    if not isinstance(message, dict):
        raise PrinterProtocolError("expected JSON object")
    return message


def probe(host: str, timeout: float = 3.0) -> PrinterIdentity:
    """Identify the device. Does not authenticate or alter printer state."""
    from .lan import require_lan_host
    host = require_lan_host(host)
    if not 0 < timeout <= 15:
        raise ValueError("timeout must be between 0 and 15 seconds")
    request = {"login": {"command": "detect", "sequence_id": "1"}}
    body = json.dumps(request, separators=(",", ":")).encode()
    frame = _FRAME_START + struct.pack("<H", len(body) + 6) + body + _FRAME_END
    with socket.create_connection((host, 3000), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(frame)
        header = _recv_exact(sock, 4)
        if not header.startswith(_FRAME_START):
            raise PrinterProtocolError("invalid response header")
        length = struct.unpack("<H", header[2:4])[0]
        if not 6 <= length <= 65535:
            raise PrinterProtocolError("invalid response length")
        response = decode_probe(header + _recv_exact(sock, length - 4))
    login = response.get("login")
    if not isinstance(login, dict) or login.get("command") != "detect":
        raise PrinterProtocolError("not a detect response")
    code = str(login.get("model", ""))
    if not code:
        raise PrinterProtocolError("missing model")
    return PrinterIdentity(
        host=host,
        model_code=code,
        model=MODEL_CODES.get(code, "Unknown Bambu Lab model"),
        firmware=str(login.get("version", "")),
        connection=str(login.get("connect", "")),
        bound=str(login.get("bind", "")),
    )


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = []
    while size:
        chunk = sock.recv(size)
        if not chunk:
            raise PrinterProtocolError("truncated response")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)
