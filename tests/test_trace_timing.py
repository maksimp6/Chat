from trace_timing import infer_response_start, request_timing_for_step, step_correlation_id


def test_step_correlation_id_is_stable():
    assert step_correlation_id("trace-123", 2) == "trace-123:step:2"
    assert step_correlation_id("trace-123", "2") == "trace-123:step:2"


def test_request_timing_prefers_latest_matching_request():
    trace = {
        "api_requests": [
            {"step": 1, "timestamp": 100.0},
            {"step": 2, "timestamp": 200.0},
            {"step": 2, "timestamp": 250.0},
        ],
        "events": [],
    }
    assert request_timing_for_step(trace, 2) == (250.0, None)


def test_request_timing_falls_back_to_completed_event_start():
    trace = {
        "api_requests": [],
        "events": [
            {"type": "api_request_completed", "payload": {"step": 3, "start_timestamp": 300.0}}
        ],
    }
    assert request_timing_for_step(trace, 3) == (300.0, None)


def test_infer_response_start_falls_back_to_previous_response_then_created():
    trace = {
        "created_at": 50.0,
        "api_requests": [],
        "events": [],
        "responses": [{"end_timestamp": 400.0}],
    }
    assert infer_response_start(trace, 4, 500.0) == 400.0

    trace["responses"] = []
    assert infer_response_start(trace, 4, 500.0) == 50.0
