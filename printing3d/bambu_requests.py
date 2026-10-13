"""Constrained Bambu MQTT request codec and secret-free capture.

Only the read-only pushall command can be emitted to real transport.
No arbitrary MQTT command forwarding is provided.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .bambu_lan import printer_report_topic

MAX_PAYLOAD = 4096


def request_topic(serial: str) -> str:
    printer_report_topic(serial)  # same validated, concrete serial
    return f"device/{serial}/request"


def encode_pushall(sequence_id: str) -> bytes:
    if not sequence_id or len(sequence_id) > 64 or not sequence_id.isascii():
        raise ValueError("invalid sequence id")
    if not all(c.isalnum() or c in "_-" for c in sequence_id):
        raise ValueError("invalid sequence id")
    return json.dumps(
        {
            "pushing": {
                "sequence_id": sequence_id,
                "command": "pushall",
                "version": 1,
                "push_target": 1,
            }
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def capture_request(topic: str, payload: bytes, serial: str) -> dict[str, Any]:
    """Safe capture: no raw packet, tokens, serial, files or arbitrary values."""
    if topic != request_topic(serial) or len(payload) > MAX_PAYLOAD:
        raise ValueError("unexpected request topic or payload length")
    try:
        obj = json.loads(payload)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("invalid JSON") from exc
    if not isinstance(obj, dict) or set(obj) != {"pushing"}:
        raise ValueError("unsupported request family")
    body = obj["pushing"]
    if not isinstance(body, dict) or set(body) != {
        "sequence_id",
        "command",
        "version",
        "push_target",
    }:
        raise ValueError("unsupported request schema")
    # Fail closed on command values; never serialize user-supplied fields.
    if body["command"] != "pushall" or body["version"] != 1 or body["push_target"] != 1:
        raise ValueError("unsupported request")
    encode_pushall(body["sequence_id"])
    return {
        "family": "pushing",
        "command": "pushall",
        "version": 1,
        "push_target": 1,
        "payload_bytes": len(payload),
    }


@dataclass
class MockBroker:
    """No network access. Records validated read-only requests only."""

    serial: str
    requested: int = 0

    def publish(self, topic: str, payload: bytes) -> dict[str, Any]:
        event = capture_request(topic, payload, self.serial)
        self.requested += 1
        return event

[executed on device: Redmi 9 (bb12d9f4-83a6-41b5-9ede-8717bac12a0a)]