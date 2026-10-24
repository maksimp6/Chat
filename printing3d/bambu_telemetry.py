"""Read-only Bambu print-report normalization and gap accounting.

The caller owns authenticated MQTT transport and trusted clock timestamps.
No printer commands or network writes are performed here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .downtime import DowntimeTracker

_BAMBU_STATES = {
    "RUNNING": "printing",
    "PREPARE": "blocked",
    "PAUSE": "blocked",
    "FINISH": "idle",
    "FAILED": "blocked",
    "IDLE": "idle",
}


def parse_bambu_report(payload: dict[str, Any]) -> str:
    """Parse only known gcode_state values, never guess missing telemetry."""
    report = payload.get("print")
    if not isinstance(report, dict):
        return "unknown"
    raw = report.get("gcode_state")
    return _BAMBU_STATES.get(raw, "unknown") if isinstance(raw, str) else "unknown"


def ingest_report(
    tracker: DowntimeTracker,
    payload: dict[str, Any],
    observed_at: float,
    *,
    event_id: str | None = None,
) -> bool:
    """Ingest an already authenticated, read-only printer report."""
    state = parse_bambu_report(payload)
    return tracker.observe(state, observed_at, event_id=event_id)


def expire_telemetry(
    tracker: DowntimeTracker, *, last_seen: float, now: float, timeout: float = 90
) -> bool:
    """Close observation coverage from timeout boundary onwards as UNKNOWN.

    An outage is not evidence the physical printer is powered off.
    """
    if timeout <= 0 or now < last_seen:
        raise ValueError("Invalid telemetry deadline")
    cutoff = last_seen + timeout
    if now < cutoff:
        return False
    snapshot = tracker.snapshot()
    events = snapshot["events"]
    if not events or events[-1]["timestamp"] >= cutoff:
        return False
    return tracker.observe("unknown", cutoff)


def period_summaries(
    tracker: DowntimeTracker,
    now: datetime,
    *,
    days: int = 7,
) -> list[dict[str, Any]]:
    """UTC daily buckets, including partial current day; no inferred telemetry."""
    if now.tzinfo is None or days < 1 or days > 366:
        raise ValueError("Aware clock and 1..366 days are required")
    now = now.astimezone(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    buckets: list[dict[str, Any]] = []
    for days_ago in range(days - 1, -1, -1):
        start = midnight - timedelta(days=days_ago)
        end = min(start + timedelta(days=1), now)
        summary = tracker.summary(start.timestamp(), end.timestamp())
        seconds = summary["seconds"]
        covered = sum(value for key, value in seconds.items() if key != "UNKNOWN")
        busy = seconds["PRINTING"]
        summary["period_start"] = start.isoformat()
        summary["period_end"] = end.isoformat()
        summary["utilization_percent"] = round(100 * busy / covered, 2) if covered else None
        buckets.append(summary)
    return buckets
