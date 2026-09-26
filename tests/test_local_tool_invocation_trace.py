from unittest.mock import patch

from invocation_context import InvocationContext
from invocation_trace import create_invocation_trace
from universal_tool_platform import UniversalToolDefinition
from yandex_client_modules.mcp_mixin import YandexMcpMixin


def _definition(name):
    return UniversalToolDefinition(
        name=name,
        description="test tool",
        input_schema={"type": "object", "properties": {}, "required": []},
        output_schema={"type": "object"},
        read_only=True,
        requires_approval=False,
        supported_transports=("responses_api",),
        executor={"type": "local"},
    )


def test_local_tool_execution_is_recorded_on_invocation_trace():
    context = InvocationContext.create("session-1", "conversation-1")
    trace = create_invocation_trace(context)
    client = object.__new__(YandexMcpMixin)
    call = {
        "type": "function_call",
        "name": "demo_tool",
        "arguments": {"value": 7},
        "call_id": "call-7",
    }

    with (
        patch(
            "yandex_client_modules.mcp_mixin.registry.get_universal_definition",
            return_value=_definition("demo_tool"),
        ),
        patch("yandex_client_modules.mcp_mixin.registry.execute", return_value={"value": 7}),
    ):
        result = client._execute_single_tool(call, [], trace=trace)

    assert result["name"] == "demo_tool"
    assert result["call_id"] == "call-7"
    assert len(trace.trace["tool_calls"]) == 1
    tool_call = trace.trace["tool_calls"][0]
    assert tool_call["name"] == "demo_tool"
    assert tool_call["call_id"] == "call-7"
    assert tool_call["arguments"] == {"value": 7}
    assert any(event["type"] == "tool_executed" for event in trace.trace["events"])


def test_local_tool_error_is_correlated_to_same_trace():
    context = InvocationContext.create("session-2", "conversation-2")
    trace = create_invocation_trace(context)
    client = object.__new__(YandexMcpMixin)
    call = {
        "type": "function_call",
        "name": "broken_tool",
        "arguments": {},
        "call_id": "call-error",
    }

    with (
        patch(
            "yandex_client_modules.mcp_mixin.registry.get_universal_definition",
            return_value=_definition("broken_tool"),
        ),
        patch(
            "yandex_client_modules.mcp_mixin.registry.execute",
            return_value={"error": "tool failed"},
        ),
    ):
        result = client._execute_single_tool(call, [], trace=trace)

    assert result["error"] == "tool failed"
    assert any(
        error.get("call_id") == "call-error" and error.get("source") == "tool:broken_tool"
        for error in trace.trace["errors"]
    )
