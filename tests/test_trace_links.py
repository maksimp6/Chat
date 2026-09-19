from trace_manager import ExecutionTrace


def test_api_step_events_share_stable_correlation_id():
    trace = ExecutionTrace("trace-123")
    trace.add_api_request({"model": "demo"}, step_index=2)
    trace.add_event("api_request_sent", {"step": 2, "correlation_id": trace.get_step_correlation_id(2)})
    trace.add_response({"id": "resp-2", "status": "completed"}, step_index=2)
    trace.add_event("api_request_completed", {"step": 2, "correlation_id": trace.get_step_correlation_id(2)})
    data = trace.finalize()

    expected = "trace-123:step:2"
    assert data["api_requests"][0]["correlation_id"] == expected
    assert data["responses"][0]["correlation_id"] == expected
    assert sum(
        1 for event in data["events"]
        if event.get("payload", {}).get("correlation_id") == expected
    ) == 4


def test_different_api_steps_do_not_share_correlation_id():
    trace = ExecutionTrace("trace-456")
    trace.add_api_request({}, step_index=1)
    trace.add_response({"id": "resp-1"}, step_index=1)
    trace.add_api_request({}, step_index=2)
    trace.add_response({"id": "resp-2"}, step_index=2)
    data = trace.finalize()

    assert data["api_requests"][0]["correlation_id"] == "trace-456:step:1"
    assert data["api_requests"][1]["correlation_id"] == "trace-456:step:2"
    assert data["responses"][0]["correlation_id"] == "trace-456:step:1"
    assert data["responses"][1]["correlation_id"] == "trace-456:step:2"
