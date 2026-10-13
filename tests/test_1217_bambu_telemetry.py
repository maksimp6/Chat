"""Bambu status ingress and telemetry timeout regression contracts."""
from datetime import datetime, timezone

import pytest

from printing3d.bambu_telemetry import (
    expire_telemetry,
    ingest_report,
    parse_bambu_report,
    period_summaries,
)
from printing3d.downtime import DowntimeTracker


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("RUNNING", "printing"),
        ("IDLE", "idle"),
        ("PAUSE", "blocked"),
        ("FAILED", "blocked"),
        ("FINISH", "idle"),
        ("UNSEEN", "unknown"),
    ],
)
def test_bambu_state_normalization(raw, expected):
    assert parse_bambu_report({"print": {"gcode_state": raw}}) == expected


def test_bambu_missing_report_unknown_not_idle():
    assert parse_bambu_report({}) == "unknown"
    assert parse_bambu_report({"print": []}) == "unknown"


def test_timeout_cuts_off_unsupported_assumptions():
    tracker = DowntimeTracker()
    assert ingest_report(tracker, {"print": {"gcode_state": "IDLE"}}, 100)
    assert not expire_telemetry(tracker, last_seen=100, now=189, timeout=90)
    assert expire_telemetry(tracker, last_seen=100, now=300, timeout=90)
    assert not expire_telemetry(tracker, last_seen=100, now=400, timeout=90)
    summary = tracker.summary(100, 300)["seconds"]
    assert summary["IDLE_READY"] == 90
    assert summary["UNKNOWN"] == 110


def test_daily_buckets_sum_to_week_and_partial_day():
    t = DowntimeTracker()
    midnight = datetime(2026, 10, 13, tzinfo=timezone.utc).timestamp()
    t.observe("printing", midnight - 3600)
    t.observe("idle", midnight + 120)
    now = datetime(2026, 10, 13, 1, tzinfo=timezone.utc)
    rows = period_summaries(t, now, days=2)
    assert len(rows) == 2
    assert rows[-1]["seconds"]["PRINTING"] == 120
    assert rows[-1]["seconds"]["IDLE_READY"] == 3480
    assert rows[-1]["utilization_percent"] == 3.33


def test_reject_backward_clock_and_naive_date():
    t = DowntimeTracker()
    with pytest.raises(ValueError):
        expire_telemetry(t, last_seen=100, now=99)
    with pytest.raises(ValueError):
        period_summaries(t, datetime(2026, 10, 13))
