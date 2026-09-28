from browser_adapters import BrowserAdapterRegistry, register_browser_tools
from trace_manager import ExecutionTrace
from universal_tool_platform import UniversalToolCall, UniversalToolDefinition, UniversalToolExecutor


class TraceAdapter:
    def execute(self, _action):
        return {
            "success": True,
            "data": {"text": "filled", "cookie": "must-not-leak"},
            "metadata": {"session_token": "must-not-leak"},
        }


class TraceToolRegistry:
    def __init__(self):
        self.definitions = {}
        self.functions = {}

    def register(self, _category, name, cfg):
        self.definitions[name] = cfg
        self.functions[name] = cfg["func"]

    def get_universal_definition(self, name):
        definition = self.definitions.get(name)
        return None if definition is None else UniversalToolDefinition.from_mapping(name, definition)

    def execute(self, name, arguments, context=None):
        return self.functions[name](arguments, context or {})


def test_browser_tool_values_and_results_are_redacted_in_execution_trace():
    registry = TraceToolRegistry()
    register_browser_tools(
        registry,
        BrowserAdapterRegistry({"browser_local": TraceAdapter()}),
    )
    trace = ExecutionTrace(trace_id="browser-trace-409")
    call = UniversalToolCall(
        tool_name="browser_local",
        arguments={"action": "fill", "target": "#password", "value": "must-not-leak"},
        call_id="browser-call-409",
        transport="local_agent",
        trace_id=trace.trace_id,
        approved=True,
    )

    result = UniversalToolExecutor(registry).execute_with_trace(call, trace)

    assert result["success"] is True
    assert result["data"]["cookie"] == "<redacted>"
    assert result["metadata"]["session_token"] == "<redacted>"
    entry = trace.trace["tool_calls"][-1]
    assert entry["call_id"] == "browser-call-409"
    assert entry["arguments"]["value"] == "<redacted>"
    assert entry["result"]["data"]["cookie"] == "<redacted>"
    assert entry["result"]["metadata"]["session_token"] == "<redacted>"
