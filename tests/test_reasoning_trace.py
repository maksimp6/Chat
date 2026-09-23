from trace_manager import ExecutionTrace


def test_reasoning_plan_is_sanitized_and_attached_to_trace():
    trace = ExecutionTrace()
    trace.set_reasoning_plan({
        "plan_id": "plan-1",
        "plan_type": "implementation",
        "steps": [{"id": "inspect"}],
        "token": "should-not-survive",
    })

    assert trace.trace["reasoning_plan"]["plan_id"] == "plan-1"
    assert trace.trace["reasoning_plan"]["steps"][0]["id"] == "inspect"
    assert "token" not in trace.trace["reasoning_plan"]
    assert any(event["type"] == "plan_created" for event in trace.trace["events"])


def test_reasoning_events_have_explicit_allowlist():
    trace = ExecutionTrace()
    trace.record_reasoning_event("reasoning_started", {"plan_id": "plan-1"})
    trace.record_reasoning_event("plan_replanned", {"reason": "test failure"})

    types = [event["type"] for event in trace.trace["events"]]
    assert "reasoning_started" in types
    assert "plan_replanned" in types


def test_unknown_reasoning_event_is_rejected():
    trace = ExecutionTrace()
    try:
        trace.record_reasoning_event("private_chain_of_thought", {})
    except ValueError as exc:
        assert "unsupported reasoning event" in str(exc)
    else:
        raise AssertionError("unsupported reasoning event was accepted")
