"""No real connector calls: deterministic budgets, polling and observation tests."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from rdc_connection.usage_control import CallLimitReached, RdcTask, ResultPending


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


def task(clock, cap=10, device="phone"):
    return RdcTask(
        owner_id="owner",
        device_id=device,
        invocation_id="trace",
        max_calls=cap,
        clock=clock.clock,
        sleep=clock.sleep,
    )


def test_demand_only_coalesces_concurrent_observation_and_fresh_request():
    session = task(Clock())
    captures = []
    assert session.calls_used == 0

    def capture():
        captures.append("image")
        return "image"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: session.observe("view-1", capture), range(20)))
    assert results == ["image"] * 20
    assert len(captures) == session.calls_used == 1
    session.observe("view-2", capture)
    assert len(captures) == 2


def test_expiry_external_change_and_device_scoping():
    clock = Clock()
    first = task(clock)
    second = task(clock, device="other")
    assert first.observe("view", lambda: "first") == "first"
    assert second.observe("view", lambda: "other") == "other"
    clock.now = 2
    assert first.observe("view", lambda: "expired") == "expired"
    first.invalidate_observation()
    assert first.observe("view", lambda: "rotated") == "rotated"


def test_mutation_preserved_exactly_once_and_invalidates_even_on_failure():
    session = task(Clock())
    session.observe("view", lambda: "before")
    events = []

    def tap():
        events.append("tap")
        raise TimeoutError("ambiguous")

    with pytest.raises(TimeoutError):
        session.call(tap, changes_device=True)
    assert events == ["tap"]
    assert session.observe("view", lambda: "after") == "after"
    assert session.calls_used == 3
    assert session.call(lambda: "status") == "status"


def test_failed_capture_not_cached_and_failures_consume_cap():
    session = task(Clock(), cap=1)

    def broken():
        raise TimeoutError

    with pytest.raises(TimeoutError):
        session.observe("view", broken)
    with pytest.raises(CallLimitReached):
        session.observe("view", lambda: "not-called")
    assert session.calls_used == 1


def test_completed_result_reuses_data_without_poll():
    session = task(Clock())
    assert (
        session.collect(
            "pid-1", "done", lambda: pytest.fail("unneeded poll"), lambda r: r == "done"
        )
        == "done"
    )
    assert session.calls_used == 0


def test_bounded_backoff_and_existing_handle_only():
    clock = Clock()
    session = task(clock)
    reads = []

    def read():
        reads.append("original-pid")
        return "running"

    with pytest.raises(ResultPending):
        session.collect("pid-1", "running", read, lambda r: r == "done", max_polls=3, timeout_s=100)
    assert reads == ["original-pid"] * 3
    assert clock.waits == [5, 10, 20]
    assert session.calls_used == 3


def test_one_delayed_read_replaces_empty_perfetto_polls():
    clock = Clock()
    session = task(clock)
    assert (
        session.collect(
            "pid-1",
            "running",
            lambda: "done" if clock.now >= 12 else "running",
            lambda r: r == "done",
            initial_wait_s=15,
        )
        == "done"
    )
    assert session.calls_used == 1
    assert clock.waits == [15]


def test_deadline_budget_and_transport_failure_stop_reads():
    clock = Clock()
    session = task(clock, cap=1)
    with pytest.raises(ResultPending):
        session.collect(
            "pid-1", "running", lambda: pytest.fail("past deadline"), lambda _: False, timeout_s=5
        )
    assert clock.waits == []
    with pytest.raises(CallLimitReached):
        session.collect("pid-1", "running", lambda: "running", lambda _: False)
    assert session.calls_used == 1

    session = task(Clock())

    def broken():
        raise TimeoutError("read failed")

    with pytest.raises(TimeoutError):
        session.collect("pid-1", "running", broken, lambda _: False)
    assert session.calls_used == 1


@pytest.mark.parametrize("cap", [0, -1, 101, True, 2.5])
def test_invalid_budget(cap):
    with pytest.raises(ValueError):
        task(Clock(), cap=cap)


@pytest.mark.parametrize(
    "options",
    [
        {"max_polls": 0},
        {"max_polls": True},
        {"timeout_s": float("nan")},
        {"timeout_s": 301},
        {"initial_wait_s": 0},
        {"initial_wait_s": float("inf")},
    ],
)
def test_invalid_poll_options(options):
    with pytest.raises(ValueError):
        task(Clock()).collect("pid-1", "running", lambda: "running", lambda _: False, **options)


@pytest.mark.parametrize("age", [-1, 6, float("nan"), float("inf")])
def test_invalid_observation_age(age):
    with pytest.raises(ValueError):
        task(Clock()).observe("view", lambda: "image", max_age_s=age)


def test_completed_handle_is_reused_but_another_job_is_not():
    clock = Clock()
    session = task(clock)
    assert session.collect("pid-1", "running", lambda: "done", lambda r: r == "done") == "done"
    assert (
        session.collect(
            "pid-1", "running", lambda: pytest.fail("duplicate read"), lambda r: r == "done"
        )
        == "done"
    )
    with pytest.raises(ResultPending):
        session.collect("pid-2", "running", lambda: "running", lambda _: False, max_polls=1)
    assert session.calls_used == 2


def test_concurrent_calls_cannot_overspend():
    session = task(Clock(), cap=2)

    def attempt(_):
        try:
            return session.call(lambda: "called")
        except CallLimitReached:
            return "blocked"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(20)))
    assert results.count("called") == session.calls_used == 2
    assert results.count("blocked") == 18


def test_missing_scope_and_observation_identity_fail_before_calls():
    with pytest.raises(ValueError):
        RdcTask(owner_id="", device_id="phone", invocation_id="trace", max_calls=1)
    session = task(Clock())
    with pytest.raises(ValueError):
        session.observe("", lambda: pytest.fail("invalid observation"))
    with pytest.raises(ValueError):
        session.collect("", "running", lambda: pytest.fail("invalid handle"), lambda _: False)
    assert session.calls_used == 0


def test_oversleep_stops_before_external_call():
    clock = Clock()
    session = RdcTask(
        owner_id="owner",
        device_id="phone",
        invocation_id="trace",
        max_calls=2,
        clock=clock.clock,
        sleep=lambda _: clock.sleep(100),
    )
    with pytest.raises(ResultPending):
        session.collect("pid", "running", lambda: pytest.fail("deadline exceeded"), lambda _: False)
    assert session.calls_used == 0


def test_zero_age_and_clock_rollback_do_not_reuse_stale_evidence():
    clock = Clock()
    session = task(clock)
    session.observe("view", lambda: "first")
    assert session.observe("view", lambda: "fresh", max_age_s=0) == "fresh"
    clock.now = -1
    assert session.observe("view", lambda: "rollback") == "rollback"
