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
    assert [item["type"] for item in first_final["billing"]["items"]] == ["ai", "compute"]


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


def test_snapshot_serializes_non_json_values_safely():
    class Opaque:
        def __repr__(self):
            return "<opaque>"

    trace = _trace()
    trace.trace["request"]["opaque"] = Opaque()

    snapshot = trace.make_snapshot()

    assert isinstance(snapshot["request"]["opaque"], str)
    assert trace.finalize()["request"]["opaque"] == snapshot["request"]["opaque"]


def test_finalized_property_reflects_frozen_state():
    trace = _trace()
    assert trace.finalized is False

    trace.finalize()

    assert trace.finalized is True


def test_registered_secret_redaction_covers_strings_and_dicts():
    trace = _trace()
    trace.register_sensitive_value("sensitive-value")

    assert trace._redact_registered_values("sensitive-value") == "<redacted>"
    assert trace._redact_registered_values(
        {"key-sensitive-value": "prior <redacted> prefix sensitive-value suffix"}
    ) == {"key-sensitive-value": "prior <redacted> prefix <redacted> suffix"}


def test_short_redaction_preserves_trace_keys_and_marker_across_snapshots():
    trace = _trace()
    trace.register_sensitive_value("e")
    trace.register_sensitive_value("redacted")
    trace.add_event("short_secret", {"value": "e"})

    first = trace.make_snapshot()
    second = trace.make_snapshot()

    assert first["trace_id"] == "snapshot-trace"
    assert first["schema_version"] == trace.SCHEMA_VERSION
    assert first["events"][-1]["type"] == "short_secret"
    assert first["events"][-1]["payload"]["value"] == "<redacted>"
    assert second["events"][-1]["payload"]["value"] == "<redacted>"
