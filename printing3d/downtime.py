"""Pure printer-downtime accounting; transport and persistence belong to the caller.

The snapshot is a JSON-safe value suitable for the canonical MemoryStore.
A sample records an observed transition, not proof of status between disconnects.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

_STATES = {
    "idle": "IDLE_READY",
    "blocked": "IDLE_BLOCKED",
    "printing": "PRINTING",
    "offline": "OFFLINE",
    "unknown": "UNKNOWN",
}
_REASONABLE = {
    "maintenance",
    "no_filament",
    "no_job",
    "awaiting_model",
    "awaiting_approval",
    "printer_error",
    "other",
    "undetermined",
}


@dataclass(frozen=True)
class Observation:
    state: str
    timestamp: float
    reason: str
    event_id: str | None


class DowntimeTracker:
    """Append-only, deterministic state intervals (not a machine controller)."""

    def __init__(self) -> None:
        self._events: list[Observation] = []
        self._ids: dict[str, Observation] = {}

    @staticmethod
    def _validate_input(state: str, timestamp: float, reason: str | None, event_id: str | None) -> None:
        if state not in _STATES:
            raise ValueError("Invalid printer state")
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise ValueError("Invalid observation timestamp")
        if not isfinite(timestamp) or timestamp < 0:
            raise ValueError("Invalid observation timestamp")
        if reason is not None and reason not in _REASONABLE:
            raise ValueError("Invalid downtime reason")
        if event_id is not None and (not isinstance(event_id, str) or not event_id):
            raise ValueError("Invalid event id")

    def observe(
        self,
        state: str,
        timestamp: float,
        *,
        reason: str | None = None,
        event_id: str | None = None,
    ) -> bool:
        self._validate_input(state, timestamp, reason, event_id)
        canonical_reason = reason if state == "blocked" and reason else "undetermined"
        entry = Observation(_STATES[state], float(timestamp), canonical_reason, event_id)
        if event_id is not None and event_id in self._ids:
            if entry != self._ids[event_id]:
                raise ValueError("Conflicting event id")
            return False
        if self._events and timestamp < self._events[-1].timestamp:
            raise ValueError("Out-of-order observation")
        if self._events and timestamp == self._events[-1].timestamp:
            if entry.state != self._events[-1].state:
                raise ValueError("Conflicting simultaneous observation")
            if entry.reason != self._events[-1].reason:
                raise ValueError("Conflicting simultaneous reason")
        self._events.append(entry)
        if event_id is not None:
            self._ids[event_id] = entry
        return True

    def summary(self, start: float, end: float) -> dict[str, Any]:
        if not (isfinite(start) and isfinite(end)) or start < 0 or end < start:
            raise ValueError("Invalid report range")
        totals = {name: 0.0 for name in _STATES.values()}
        reasons: dict[str, float] = {}
        cursor = float(start)
        current = "UNKNOWN"
        reason = "undetermined"
        for event in self._events:
            if event.timestamp <= start:
                current, reason = event.state, event.reason
                continue
            if event.timestamp >= end:
                break
            duration = event.timestamp - cursor
            totals[current] += duration
            if current in ("IDLE_READY", "IDLE_BLOCKED"):
                reasons[reason] = reasons.get(reason, 0.0) + duration
            cursor = event.timestamp
            current, reason = event.state, event.reason
        duration = float(end) - cursor
        totals[current] += duration
        if current in ("IDLE_READY", "IDLE_BLOCKED") and duration:
            reasons[reason] = reasons.get(reason, 0.0) + duration
        return {"seconds": totals, "reasons": reasons}

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "events": [
                {
                    "state": event.state,
                    "timestamp": event.timestamp,
                    "reason": event.reason,
                    "event_id": event.event_id,
                }
                for event in self._events
            ],
        }

    @classmethod
    def from_snapshot(cls, data: dict[str, Any]) -> DowntimeTracker:
        if data.get("schema") != 1 or not isinstance(data.get("events"), list):
            raise ValueError("Invalid downtime snapshot")
        result = cls()
        inverse = {value: key for key, value in _STATES.items()}
        for record in data["events"]:
            if not isinstance(record, dict) or record.get("state") not in inverse:
                raise ValueError("Invalid downtime event")
            result.observe(
                inverse[record["state"]],
                record["timestamp"],
                reason=record["reason"] if record["state"] == "IDLE_BLOCKED" else None,
                event_id=record["event_id"],
            )
        return result
