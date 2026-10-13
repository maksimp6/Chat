"""Contract regression tests for #1217 printer downtime intervals."""
from printing3d.downtime import DowntimeTracker


def test_ready_idle_and_printing_are_disjoint():
    t = DowntimeTracker()
    t.observe("idle", 0)
    t.observe("printing", 10)
    t.observe("idle", 30)
    assert t.summary(0, 40)["seconds"]["IDLE_READY"] == 20
    assert t.summary(0, 40)["seconds"]["PRINTING"] == 20


def test_offline_is_not_proven_idle():
    t = DowntimeTracker()
    t.observe("offline", 0)
    t.observe("idle", 60)
    result = t.summary(0, 120)
    assert result["seconds"]["OFFLINE"] == 60
    assert result["seconds"]["IDLE_READY"] == 60


def test_missing_telemetry_unknown_and_no_fabricated_reason():
    t = DowntimeTracker()
    t.observe("idle", 10)
    report = t.summary(0, 20)
    assert report["seconds"]["UNKNOWN"] == 10
    assert report["reasons"] == {"undetermined": 10}


def test_repeat_event_is_idempotent_and_conflict_is_rejected():
    t = DowntimeTracker()
    assert t.observe("idle", 1, event_id="a")
    assert not t.observe("idle", 1, event_id="a")
    try:
        t.observe("printing", 1, event_id="a")
    except ValueError:
        pass
    else:
        assert False, "Conflicting retry must fail"


def test_out_of_order_event_fails_closed():
    t = DowntimeTracker()
    t.observe("idle", 50)
    try:
        t.observe("printing", 49)
    except ValueError:
        pass
    else:
        assert False, "Clock regression must fail"


def test_snapshot_replay_without_double_counting():
    t = DowntimeTracker()
    t.observe("blocked", 0, reason="maintenance", event_id="a")
    t.observe("printing", 20, event_id="b")
    restored = DowntimeTracker.from_snapshot(t.snapshot())
    assert restored.observe("printing", 20, event_id="b") is False
    assert restored.summary(0, 30) == t.summary(0, 30)


def test_window_clipping_and_no_future_assumptions():
    t = DowntimeTracker()
    t.observe("idle", 10)
    t.observe("printing", 30)
    assert t.summary(20, 25)["seconds"]["IDLE_READY"] == 5
    assert t.summary(30, 30)["seconds"]["PRINTING"] == 0


def test_blocked_reason_only_for_blocked():
    t = DowntimeTracker()
    t.observe("blocked", 0, reason="no_filament")
    t.observe("idle", 10, reason="no_filament")
    assert t.summary(0, 20)["reasons"] == {"no_filament": 10, "undetermined": 10}


def test_invalid_states_and_negative_timestamps():
    t = DowntimeTracker()
    for state, ts in [("nonsense", 0), ("idle", -1)]:
        try:
            t.observe(state, ts)
        except ValueError:
            continue
        assert False, "Invalid input accepted"
