import json

from invocation_context import InvocationContext
from invocation_trace import create_invocation_trace
from mcp_trace import record_yandex_mcp_activity


def test_yandex_mcp_activity_is_correlated_with_invocation_trace():
    context = InvocationContext("session-1", "conversation-1", "invocation-1", "trace-1")
    trace = create_invocation_trace(context)
    trace.add_response({
        "id": "resp-1",
        "status": "completed",
        "output": [
            {
                "type": "mcp_call",
                "id": "mcp-call-1",
                "server_label": "github",
                "name": "search_repositories",
                "arguments": {"query": "alice"},
                "authorization": "super-secret",
            }
        ],
    }, step_index=1)

    assert record_yandex_mcp_activity(trace) == 1
    data = trace.finalize()

    assert data["context"]["invocation_id"] == "invocation-1"
    assert data["context"]["trace_id"] == "trace-1"
    assert data["mcp_activity"][0]["type"] == "mcp_call"
    assert data["mcp_activity"][0]["data"]["authorization"] == "<redacted>"
    assert any(event["type"] == "mcp_activity_observed" for event in data["events"])


def test_mcp_activity_from_poll_snapshot_is_deduplicated():
    context = InvocationContext("s", "c", "i", "t")
    trace = create_invocation_trace(context)
    response = {
        "id": "resp-2",
        "status": "completed",
        "output": [{"type": "mcp_call", "id": "call-2", "server_label": "files", "name": "read_file"}],
    }
    trace.add_response(response, step_index=1, kind="initial_response")
    trace.add_response(response, step_index=1, kind="poll_response")

    assert record_yandex_mcp_activity(trace) == 1
    assert len(trace.trace["mcp_activity"]) == 1
    json.dumps(trace.finalize(), ensure_ascii=False)


def test_non_mcp_response_is_ignored():
    context = InvocationContext("s", "c", "i", "t")
    trace = create_invocation_trace(context)
    trace.add_response({"id": "resp-3", "status": "completed", "output": [{"type": "message", "content": []}]})

    assert record_yandex_mcp_activity(trace) == 0
    assert trace.trace.get("mcp_activity") == []
