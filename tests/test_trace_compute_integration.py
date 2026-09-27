"""Tests for compute resource tracking integration with ExecutionTrace."""

import pytest


def test_trace_finalize_includes_compute_costs():
    from trace_manager import ExecutionTrace

    trace = ExecutionTrace(trace_id="trace-compute-1")
    trace.set_context(
        invocation_id="inv-1",
        session_id="session-1",
        user_id="user-1",
    )
    trace.add_response(
        {
            "model": "aliceai-llm",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
            },
        },
        step_index=1,
        start_timestamp=0.0,
        end_timestamp=0.3,
        timing_ms=300,
    )

    for i in range(2):
        trace.track_tool_execution(
            f"test_tool_{i}",
            {"param": "value"},
            lambda: {"result": "ok"},
        )

    finalized = trace.finalize()

    assert finalized["billing"]["cost_status"] == "calculated"
    assert "items" in finalized["billing"]
    assert len(finalized["billing"]["items"]) >= 2

    has_ai_item = any(item.get("type") == "ai" for item in finalized["billing"]["items"])
    has_compute_item = any(item.get("type") == "compute" for item in finalized["billing"]["items"])

    assert has_ai_item, "Should have AI billing item"
    assert has_compute_item, "Should have compute billing item"

    assert finalized["billing"]["total_cost"] > 0
    assert finalized["billing"]["compute_cost"] > 0


def test_trace_finalize_compute_with_empty_trace():
    from trace_manager import ExecutionTrace

    trace = ExecutionTrace(trace_id="trace-empty")
    trace.set_context(user_id="user-1")

    finalized = trace.finalize()

    assert finalized["billing"]["cost_status"] == "calculated"
    compute_items = [item for item in finalized["billing"]["items"] if item.get("type") == "compute"]
    assert len(compute_items) > 0
    assert compute_items[0]["total_cost"] == 0.0


def test_compute_costs_breakdown_in_billing():
    from trace_manager import ExecutionTrace

    trace = ExecutionTrace(trace_id="trace-breakdown")
    trace.set_context(user_id="user-1")

    trace.add_response(
        {"model": "aliceai-llm", "usage": {"input_tokens": 100, "output_tokens": 50}},
        step_index=1,
        timing_ms=500,
    )
    trace.track_tool_execution(
        "tool1",
        {},
        lambda: {"ok": True},
    )

    finalized = trace.finalize()
    billing = finalized["billing"]

    assert "tool_cost" in billing
    assert "compute_cost" in billing
    assert billing["total_cost"] > 0

    compute_items = [item for item in billing["items"] if item.get("type") == "compute"]
    assert len(compute_items) > 0
    assert "cpu_cost" in compute_items[0]
    assert "tool_cost" in compute_items[0]


def test_multiple_compute_operations_accumulate():
    from trace_manager import ExecutionTrace

    trace = ExecutionTrace(trace_id="trace-multi")
    trace.set_context(user_id="user-1")

    for i in range(3):
        trace.track_tool_execution(
            f"tool{i}",
            {},
            lambda: {"result": i},
        )

    finalized = trace.finalize()
    billing = finalized["billing"]
    compute_items = [item for item in billing["items"] if item.get("type") == "compute"]

    assert len(compute_items) > 0
    assert compute_items[0]["type"] == "compute"
    assert "cpu_time_ms" in compute_items[0]
    assert "tool_time_ms" in compute_items[0]
