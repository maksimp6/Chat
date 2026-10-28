"""Typed log event adapter for a durable Memory DB writer.

The writer must acknowledge only after its durable barrier. This module never
falls back to process-local MemoryDatabase or SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Callable, Protocol
from uuid import uuid4

_SECRET = re.compile(
    r"(?i)(bearer\s+)[^\s,;]+|((?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+"
)
MAX_EVENT_BYTES = 64 * 1024


class DurableLogWriter(Protocol):
    def append(self, stream: str, event_id: str, payload: bytes) -> None:
        """Synchronously commit one idempotent event, or raise on failure."""


@dataclass(frozen=True)
class LogEvent:
    event_id: str
    trace_id: str
    source: str
    level: str
    timestamp: str
    message: str


def redact(value: str) -> str:
    return _SECRET.sub(
        lambda match: (match.group(1) or match.group(2)) + "[REDACTED]", value
    )


class MemoryLogAdapter:
    """Explicit opt-in boundary; no silent best-effort persistence."""

    def __init__(self, writer: DurableLogWriter):
        self._writer = writer

    def append(
        self,
        *,
        trace_id: str,
        source: str,
        level: str,
        message: str,
        event_id: str | None = None,
    ) -> LogEvent:
        if not trace_id or not source:
            raise ValueError("trace_id and source are required")
        event = LogEvent(
            event_id=event_id or str(uuid4()),
            trace_id=trace_id,
            source=source,
            level=level.upper(),
            timestamp=datetime.now(timezone.utc).isoformat(),
            message=redact(message),
        )
        payload = json.dumps(event.__dict__, ensure_ascii=False, sort_keys=True).encode(
            "utf-8"
        )
        if len(payload) > MAX_EVENT_BYTES:
            raise ValueError("log event exceeds maximum frame size")
        self._writer.append("execution_logs", event.event_id, payload)
        return event
