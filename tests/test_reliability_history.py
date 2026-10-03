from datetime import UTC, datetime, timedelta

import pytest

from agent_office.reliability_history import (
    build_reliability_snapshot,
    deduplicate_events,
    export_window,
    normalize_event,
    rolling_events,
)


NOW = datetime(2026, 9, 30, 7, 0, tzinfo=UTC)


def raw(event_id, *, minutes_ago=0, status="valid", provenance=("github:x",), **extra):
    value = {
        "id": event_id,
        "at": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
        "kind": "merge_readiness",
        "status": status,
        "provenance": list(provenance),
    }
    value.update(extra)
    return value


def test_normalize_event_is_bounded_and_canonical():
    event = normalize_event(
        raw("x", provenance=(" github:b ", "github:a", "github:a"), status="MAYBE")
    )
    assert event.event_id == "x"
    assert event.status == "unknown"
    assert event.provenance == ("github:a", "github:b")


def test_normalize_event_requires_identity_and_kind():
    with pytest.raises(ValueError, match="id"):
        normalize_event({**raw("x"), "id": ""})
    with pytest.raises(ValueError, match="kind"):
        normalize_event({**raw("x"), "kind": ""})


def test_deduplicate_keeps_latest_stable_event():
    old = normalize_event(raw("same", minutes_ago=10, status="invalid"))
    new = normalize_event(raw("same", minutes_ago=1, status="valid"))
    result = deduplicate_events([new, old])
    assert len(result) == 1
    assert result[0].status == "valid"


def test_conflicting_same_timestamp_duplicate_fails_closed():
    first = normalize_event(raw("same", status="valid", provenance=("github:a",)))
    second = normalize_event(raw("same", status="invalid", provenance=("github:b",)))
    result = deduplicate_events([first, second])
    assert result[0].status == "unknown"
    assert result[0].violation_code == "conflicting_duplicate"
    assert result[0].provenance == ("github:a", "github:b")


def test_rolling_window_excludes_old_and_future_events():
    current = normalize_event(raw("current"))
    old = normalize_event(
        {
            **raw("old"),
            "at": (NOW - timedelta(days=31)).isoformat(),
        }
    )
    future = normalize_event(
        {
            **raw("future"),
            "at": (NOW + timedelta(minutes=1)).isoformat(),
        }
    )
    result = rolling_events([future, old, current], generated_at=NOW)
    assert [event.event_id for event in result] == ["current"]


def test_rolling_window_rejects_invalid_window():
    with pytest.raises(ValueError, match="window_days"):
        rolling_events([], generated_at=NOW, window_days=0)


def test_snapshot_contains_only_unique_window_decisions_and_real_metadata():
    snapshot = build_reliability_snapshot(
        [
            raw("a", minutes_ago=5),
            raw("a", minutes_ago=1),
            raw("b", minutes_ago=2, status="invalid"),
        ],
        generated_at=NOW,
        observer_last_success_at=NOW - timedelta(minutes=15),
        maintainer_handoffs=[{"id": "pr:1", "observer_passes": 1, "outcome": ""}],
        strong_retries_without_new_evidence=2,
        duplicate_exact_context_reads=3,
    )
    assert snapshot["window_event_ids"] == ["b", "a"]
    assert len(snapshot["decisions"]) == 2
    assert snapshot["observer_last_success_at"].endswith("+00:00")
    assert snapshot["strong_retries_without_new_evidence"] == 2
    assert snapshot["duplicate_exact_context_reads"] == 3


def test_snapshot_preserves_unknown_observer_evidence():
    snapshot = build_reliability_snapshot(
        [raw("a")],
        generated_at=NOW,
        observer_last_success_at=None,
    )
    assert snapshot["observer_last_success_at"] is None


def test_export_window_is_json_serializable_shape():
    exported = export_window([normalize_event(raw("a"))])
    assert exported[0]["at"].endswith("+00:00")
    assert exported[0]["provenance"] == ["github:x"]


def test_conflicting_duplicate_is_order_independent():
    first = normalize_event({**raw("same", provenance=("github:a",)), "kind": "merge_readiness"})
    second = normalize_event(
        {
            **raw("same", status="invalid", provenance=("trace:b",)),
            "kind": "approval_boundary",
        }
    )

    forward = deduplicate_events([first, second])
    reverse = deduplicate_events([second, first])

    assert forward == reverse
    assert forward[0].kind == "conflicting_duplicate"
    assert forward[0].status == "unknown"
    assert forward[0].provenance == ("github:a", "trace:b")


def test_naive_event_timestamp_is_normalized_to_utc():
    event = normalize_event(
        {
            **raw("naive"),
            "at": NOW.replace(tzinfo=None).isoformat(),
        }
    )
    assert event.at.tzinfo is UTC
    assert event.at == NOW
