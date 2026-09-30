"""Rolling evidence window for the Alice Pro reliability SLO."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable


VALID_STATUSES = {"valid", "invalid", "unknown"}


@dataclass(frozen=True)
class DecisionEvent:
    event_id: str
    at: datetime
    kind: str
    status: str
    provenance: tuple[str, ...]
    violation_code: str = ""
    catastrophic: bool = False

    def as_reliability_decision(self) -> dict[str, Any]:
        return {
            "id": self.event_id,
            "kind": self.kind,
            "status": self.status,
            "provenance": list(self.provenance),
            "violation_code": self.violation_code,
            "catastrophic": self.catastrophic,
        }


def _parse_time(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def normalize_event(raw: dict[str, Any]) -> DecisionEvent:
    event_id = str(raw.get("id") or "").strip()
    kind = str(raw.get("kind") or "").strip()
    status = str(raw.get("status") or "unknown").strip().lower()
    if not event_id:
        raise ValueError("decision event id is required")
    if not kind:
        raise ValueError("decision event kind is required")
    if status not in VALID_STATUSES:
        status = "unknown"

    provenance = tuple(
        sorted({str(item).strip() for item in raw.get("provenance") or [] if str(item).strip()})
    )
    return DecisionEvent(
        event_id=event_id,
        at=_parse_time(raw["at"]),
        kind=kind,
        status=status,
        provenance=provenance,
        violation_code=str(raw.get("violation_code") or "").strip(),
        catastrophic=bool(raw.get("catastrophic")),
    )


def deduplicate_events(events: Iterable[DecisionEvent]) -> tuple[DecisionEvent, ...]:
    latest: dict[str, DecisionEvent] = {}
    for event in events:
        previous = latest.get(event.event_id)
        if previous is None or event.at > previous.at:
            latest[event.event_id] = event
            continue
        if event.at == previous.at and event != previous:
            latest[event.event_id] = DecisionEvent(
                event_id=event.event_id,
                at=event.at,
                kind=event.kind,
                status="unknown",
                provenance=tuple(sorted(set(event.provenance + previous.provenance))),
                violation_code="conflicting_duplicate",
                catastrophic=event.catastrophic or previous.catastrophic,
            )
    return tuple(sorted(latest.values(), key=lambda item: (item.at, item.event_id)))


def rolling_events(
    events: Iterable[DecisionEvent],
    *,
    generated_at: datetime,
    window_days: int = 30,
) -> tuple[DecisionEvent, ...]:
    if window_days < 1:
        raise ValueError("window_days must be at least 1")
    now = _parse_time(generated_at)
    cutoff = now - timedelta(days=window_days)
    selected = [
        event
        for event in deduplicate_events(events)
        if cutoff <= event.at <= now
    ]
    return tuple(selected)


def build_reliability_snapshot(
    raw_events: Iterable[dict[str, Any]],
    *,
    generated_at: datetime,
    observer_last_success_at: datetime | None,
    maintainer_handoffs: Iterable[dict[str, Any]] = (),
    strong_retries_without_new_evidence: int = 0,
    duplicate_exact_context_reads: int = 0,
    window_days: int = 30,
) -> dict[str, Any]:
    normalized = [normalize_event(raw) for raw in raw_events]
    window = rolling_events(normalized, generated_at=generated_at, window_days=window_days)
    return {
        "generated_at": _parse_time(generated_at).isoformat(),
        "observer_last_success_at": (
            _parse_time(observer_last_success_at).isoformat()
            if observer_last_success_at is not None
            else None
        ),
        "decisions": [event.as_reliability_decision() for event in window],
        "maintainer_handoffs": [dict(item) for item in maintainer_handoffs],
        "strong_retries_without_new_evidence": int(strong_retries_without_new_evidence),
        "duplicate_exact_context_reads": int(duplicate_exact_context_reads),
        "window_days": int(window_days),
        "window_event_ids": [event.event_id for event in window],
    }


def export_window(events: Iterable[DecisionEvent]) -> list[dict[str, Any]]:
    return [
        {
            **asdict(event),
            "at": event.at.isoformat(),
            "provenance": list(event.provenance),
        }
        for event in events
    ]
