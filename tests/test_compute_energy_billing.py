import threading
import time

import pytest

from compute_resources import build_compute_billing_item, get_energy_pricing


@pytest.fixture(autouse=True)
def _clear_energy_env(monkeypatch):
    monkeypatch.delenv("ALICE_CPU_WATTS_PER_CORE", raising=False)
    monkeypatch.delenv("ALICE_ELECTRICITY_PRICE_RUB_PER_KWH", raising=False)


def test_energy_is_cpu_seconds_times_watts():
    item = build_compute_billing_item(
        cpu_seconds=3600.0,
        wall_seconds=7200.0,
        pricing={"watts_per_core": 20.0, "price_per_kwh": 6.0},
    )

    assert item["energy_joules"] == pytest.approx(72000.0)
    assert item["energy_wh"] == pytest.approx(20.0)
    assert item["total_cost"] == pytest.approx(0.12)
    assert item["cpu_utilization"] == pytest.approx(0.5)
    assert item["cost_status"] == "calculated"


def test_without_electricity_price_energy_is_recorded_but_not_billed():
    item = build_compute_billing_item(cpu_seconds=1.8)

    assert item["watts_per_core"] == 15.0
    assert item["energy_wh"] == pytest.approx(0.0075)
    assert item["total_cost"] == 0.0
    assert item["cost_status"] == "not_billed"
    assert item["cost_reason"] == "electricity_price_not_configured"


def test_unmeasured_cpu_time_is_not_billed():
    item = build_compute_billing_item(None, pricing={"watts_per_core": 15.0, "price_per_kwh": 6.0})

    assert item["cost_status"] == "not_billed"
    assert item["energy_wh"] is None
    assert item["total_cost"] == 0.0


@pytest.mark.parametrize("raw", ["abc", "-1", "nan", "inf"])
def test_invalid_env_values_are_ignored(monkeypatch, raw):
    monkeypatch.setenv("ALICE_CPU_WATTS_PER_CORE", raw)
    monkeypatch.setenv("ALICE_ELECTRICITY_PRICE_RUB_PER_KWH", raw)

    assert get_energy_pricing() == {"watts_per_core": 15.0, "price_per_kwh": None}


def test_not_billed_compute_does_not_make_ai_billing_partial():
    from billing import aggregate_billing

    ai = {"type": "ai", "cost_status": "calculated", "total_cost": 0.11}
    compute = build_compute_billing_item(2.0)

    result = aggregate_billing([ai, compute])

    assert result["cost_status"] == "calculated"
    assert result["total_cost"] == pytest.approx(0.11)
    assert result["compute_cost"] == 0.0
    assert result["cpu_seconds"] == pytest.approx(2.0)


def test_calculated_compute_cost_is_added_to_total():
    from billing import aggregate_billing

    ai = {"type": "ai", "cost_status": "calculated", "total_cost": 0.11}
    compute = build_compute_billing_item(
        3600.0, pricing={"watts_per_core": 10.0, "price_per_kwh": 5.0}
    )

    result = aggregate_billing([ai, compute])

    assert result["compute_cost"] == pytest.approx(0.05)
    assert result["total_cost"] == pytest.approx(0.16)
    assert result["energy_wh"] == pytest.approx(10.0)


def test_waiting_is_not_billed_as_compute():
    from trace_manager import ExecutionTrace

    trace = ExecutionTrace()
    time.sleep(0.2)
    billing = trace.finalize()["billing"]

    assert billing["cpu_seconds"] < 0.1
    assert trace.trace["timings"]["total_duration_ms"] >= 200


def test_cpu_work_is_measured():
    from trace_manager import ExecutionTrace

    trace = ExecutionTrace()
    deadline = time.thread_time() + 0.05
    while time.thread_time() < deadline:
        pass
    billing = trace.finalize()["billing"]

    assert billing["cpu_seconds"] >= 0.05
    assert billing["energy_wh"] > 0


def test_repeated_finalize_bills_compute_once(monkeypatch):
    monkeypatch.setenv("ALICE_ELECTRICITY_PRICE_RUB_PER_KWH", "6")
    from trace_manager import ExecutionTrace

    trace = ExecutionTrace()
    first = trace.finalize()["billing"]
    second = trace.finalize()["billing"]

    compute_items = [i for i in second["items"] if i.get("type") == "compute"]
    assert len(compute_items) == 1
    assert second["compute_cost"] == first["compute_cost"]


def test_finalize_from_other_thread_is_not_attributed():
    from trace_manager import ExecutionTrace

    trace = ExecutionTrace()
    result = {}
    worker = threading.Thread(target=lambda: result.update(trace.finalize()))
    worker.start()
    worker.join()

    compute = [i for i in result["billing"]["items"] if i.get("type") == "compute"][0]
    assert compute["cost_reason"] == "cpu_time_unmeasured"


def test_tool_call_records_cpu_ms():
    from trace_manager import ExecutionTrace

    trace = ExecutionTrace()
    trace.track_tool_execution("noop", {}, lambda: {"ok": True})

    assert trace.trace["tool_calls"][0]["cpu_ms"] >= 0
