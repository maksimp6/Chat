from datetime import datetime, timezone

from trace_manager import ExecutionTrace


def test_trace_records_provider_key_identity_without_secret():
    trace = ExecutionTrace(trace_id="trace-key-test")
    secret = "sk-test-secret"
    trace.set_provider_key(
        "aje123provider",
        fingerprint="a" * 64,
        issued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
        project_id="project-1",
        source="global",
    )
    trace.add_api_request({"model": "gpt://project/model/latest"}, step_index=1)

    result = trace.finalize()

    assert result["provider_key"]["key_id"] == "aje123provider"
    assert result["provider_keys"][0]["key_id"] == "aje123provider"
    assert result["context"]["provider_key_id"] == "aje123provider"
    assert result["api_requests"][0]["provider_key_id"] == "aje123provider"
    assert secret not in str(result)
