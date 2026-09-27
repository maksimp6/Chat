"""Regression coverage for the read-only ExecutionTrace snapshot lifecycle."""

from unittest.mock import patch

import pytest

from trace_manager import ExecutionTrace


def _trace() -> ExecutionTrace:
    trace = ExecutionTrace(trace_id="snapshot-trace")
    trace.set_context(invocation_id="invocation-1", user_id="owner-1")
    trace.add_response(
        {
            "id": "response-1",
            "model": "aliceai-llm",
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        }
    )
    return trace


def test_repeated_snapshots_are_stable_and_do_not_append_billing_items():
    trace = _trace()
    first = trace.make_snapshot()

    for _ in range(100):
        assert trace.make_snapshot()["billing"] == first["billing"]

    assert len(trace.trace["billing"]["items"]) == 1
    assert "total_duration_ms" not in trace.trace["timings"]


def test_final_billing_matches_snapshot_and_finalize_is_idempotent():
    trace = _trace()
    snapshot = trace.make_snapshot()
    first_final = trace.finalize()
    second_final = trace.finalize()

    assert first_final["billing"] == snapshot["billing"]
    assert second_final == first_final
    assert len(first_final["billing"]["items"]) == 1


def test_snapshot_and_final_results_are_deep_independent():
    trace = _trace()
    snapshot = trace.make_snapshot()
    snapshot["billing"]["items"][0]["input_tokens"] = 999
    snapshot["responses"][0]["raw"]["usage"]["input_tokens"] = 999

    assert trace.trace["billing"]["items"][0]["input_tokens"] == 100
    assert trace.trace["responses"][0]["raw"]["usage"]["input_tokens"] == 100

    finalized = trace.finalize()
    finalized["billing"]["items"][0]["input_tokens"] = 777
    assert trace.finalize()["billing"]["items"][0]["input_tokens"] == 100


def test_failed_final_rebuild_keeps_trace_mutable():
    trace = _trace()
    with patch("billing.aggregate_billing", side_effect=RuntimeError("billing unavailable")):
        with pytest.raises(RuntimeError, match="billing unavailable"):
            trace.finalize()

    trace.set_request({"message": "retry is allowed"})
    assert trace.finalize()["request"]["message"] == "retry is allowed"


def test_mutation_after_finalization_is_rejected_and_cannot_change_frozen_result():
    trace = _trace()
    frozen = trace.finalize()

    with pytest.raises(RuntimeError, match="finalized"):
        trace.add_response({"id": "late", "usage": {"input_tokens": 1}})

    assert trace.make_snapshot() == frozen
