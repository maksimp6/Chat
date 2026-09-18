import sys
import types

from universal_tool_platform import (
    UniversalToolCall,
    UniversalToolExecutor,
    UniversalToolResult,
    validate_json_schema,
)


class FakeRegistry:
    def __init__(self, cfg, result=None):
        self.cfg = cfg
        self.result = result if result is not None else {"ok": True}

    def get_universal_definition(self, name):
        if name != self.cfg["name"]:
            return None
        return self.cfg

    def execute(self, name, arguments, **kwargs):
        assert name == self.cfg["name"]
        assert arguments == {"value": "ok"}
        return self.result


def definition(**overrides):
    result = {
        "name": "demo",
        "title": "Demo",
        "description": "Demo tool",
        "inputSchema": {
            "type": "object",
            "properties": {"value": {"type": "string", "minLength": 2}},
            "required": ["value"],
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object"},
        "capabilities": ["test"],
        "risk_level": "low",
        "read_only": True,
        "requires_approval": False,
        "supported_transports": ["mcp", "responses_api"],
        "executor": {"type": "local"},
    }
    result.update(overrides)
    return result


def test_validate_json_schema_rejects_missing_and_unknown_properties():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "minLength": 3}},
        "required": ["name"],
        "additionalProperties": False,
    }
    errors = validate_json_schema({"extra": 1}, schema)
    assert any("required property" in error for error in errors)
    assert any("additional property" in error for error in errors)


def test_executor_validates_and_normalizes_legacy_result():
    registry = FakeRegistry(definition(), result={"message": "done"})
    result = UniversalToolExecutor(registry).execute(
        UniversalToolCall(
            tool_name="demo",
            arguments={"value": "ok"},
            transport="mcp",
        )
    )
    assert result["success"] is True
    assert result["data"] == {"message": "done"}
    assert result["metadata"]["tool"] == "demo"
    assert result["metadata"]["executor"] == "local"


def test_executor_rejects_invalid_input_before_execution():
    called = {"value": False}

    class Registry(FakeRegistry):
        def execute(self, name, arguments, **kwargs):
            called["value"] = True
            return {"ok": True}

    result = UniversalToolExecutor(Registry(definition())).execute(
        UniversalToolCall(
            tool_name="demo",
            arguments={"value": "x"},
            transport="mcp",
        )
    )
    assert result["success"] is False
    assert result["metadata"]["phase"] == "validation"
    assert called["value"] is False


def test_executor_enforces_transport_and_approval():
    transport_result = UniversalToolExecutor(
        FakeRegistry(definition(supported_transports=["responses_api"]))
    ).execute(
        UniversalToolCall(
            tool_name="demo",
            arguments={"value": "ok"},
            transport="mcp",
        )
    )
    assert transport_result["success"] is False
    assert transport_result["metadata"]["phase"] == "transport"

    approval_result = UniversalToolExecutor(
        FakeRegistry(definition(requires_approval=True))
    ).execute(
        UniversalToolCall(
            tool_name="demo",
            arguments={"value": "ok"},
            transport="mcp",
        )
    )
    assert approval_result["success"] is False
    assert approval_result["metadata"]["phase"] == "approval_required"


def test_executor_accepts_approval_hook():
    registry = FakeRegistry(definition(requires_approval=True))
    result = UniversalToolExecutor(registry).execute(
        UniversalToolCall(
            tool_name="demo",
            arguments={"value": "ok"},
            transport="mcp",
        ),
        approval=lambda definition, call: True,
    )
    assert result["success"] is True


def test_executor_remote_target_waits_for_gateway_result(monkeypatch):
    calls = []

    fake_gateway = types.ModuleType("local_agent_gateway")

    def enqueue(agent_id, tool_name, arguments, **kwargs):
        calls.append(("enqueue", agent_id, tool_name, arguments, kwargs))
        return "job-1"

    def wait(job_id, *, timeout_seconds):
        calls.append(("wait", job_id, timeout_seconds))
        return {
            "success": True,
            "data": {"device": "android"},
            "error": None,
            "metadata": {"agent_id": "phone-1"},
        }

    fake_gateway.enqueue_local_tool_job = enqueue
    fake_gateway.wait_for_local_tool_job = wait
    monkeypatch.setitem(sys.modules, "local_agent_gateway", fake_gateway)

    cfg = definition(
        supported_transports=["responses_api"],
        executor={"type": "remote_local_agent", "agent_id": "phone-1", "timeout_seconds": 7},
    )
    result = UniversalToolExecutor(FakeRegistry(cfg)).execute(
        UniversalToolCall(
            tool_name="demo",
            arguments={"value": "ok"},
            transport="responses_api",
            trace_id="trace-1",
            invocation_id="inv-1",
        )
    )
    assert result["success"] is True
    assert result["data"] == {"device": "android"}
    assert calls[0][0] == "enqueue"
    assert calls[1] == ("wait", "job-1", 7.0)
