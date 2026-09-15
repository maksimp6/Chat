import json

from trace_manager import ExecutionTrace


def test_finalize_handles_execution_trace_circular_reference():
    trace = ExecutionTrace()
    trace.trace["request"]["execution_trace"] = trace

    result = trace.finalize()

    assert result["trace_id"] == trace.trace_id
    assert result["request"]["execution_trace"] == {
        "trace_id": trace.trace_id,
        "<circular_ref>": True,
    }
    json.dumps(result)
