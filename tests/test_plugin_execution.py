import json

import pytest

from invocation_context import InvocationContext
from plugin_execution import PluginExecutionGateway
from plugin_manager import PluginError, PluginManager
from runtime import RuntimeDispatcher


class Registry:
    def __init__(self):
        self.calls = []

    def get_universal_definition(self, name):
        if name != "echo":
            return None
        return {
            "name": "echo",
            "description": "Echo text",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            "outputSchema": {"type": "object"},
            "capabilities": ["messages.read"],
            "requires_approval": False,
        }

    def execute(self, name, arguments, **kwargs):
        self.calls.append((name, arguments, kwargs))
        return {"echo": arguments["text"]}


def manager_for(tmp_path, permissions):
    folder = tmp_path / "demo"
    folder.mkdir()
    (folder / "plugin.json").write_text(
        json.dumps(
            {
                "api_version": 1,
                "id": "demo",
                "name": "Demo",
                "version": "1.0.0",
                "capabilities": ["messages.read"],
                "permissions": permissions,
            }
        ),
        encoding="utf-8",
    )
    manager = PluginManager(tmp_path)
    manager.discover()
    manager.enable("demo")
    return manager


def invocation():
    return InvocationContext(
        session_id="session-1",
        conversation_id="conversation-1",
        invocation_id="invocation-1",
        trace_id="trace-1",
        user_id="user-1",
    )


def test_permission_denial_never_reaches_registry(tmp_path):
    registry = Registry()
    gateway = PluginExecutionGateway(
        manager_for(tmp_path, []), registry, RuntimeDispatcher()
    )
    result = gateway.execute("demo", "echo", {"text": "hello"}, invocation())
    assert result["success"] is False
    assert result["metadata"]["phase"] == "authorization"
    assert "tool:echo" in result["error"]
    assert registry.calls == []


def test_successful_execution_is_scoped_and_correlated(tmp_path):
    registry = Registry()
    gateway = PluginExecutionGateway(
        manager_for(tmp_path, ["tool:echo"]), registry, RuntimeDispatcher()
    )
    result = gateway.execute("demo", "echo", {"text": "hello"}, invocation())
    assert result["success"] is True
    assert result["data"] == {"echo": "hello"}
    assert result["metadata"]["plugin_id"] == "demo"
    assert result["metadata"]["plugin_runtime_id"] == "plugin:demo:invocation-1"
    assert result["metadata"]["trace_id"] == "trace-1"
    call = registry.calls[0][2]["context"]["call"]
    assert call.invocation_id == "invocation-1"
    assert call.metadata["plugin_runtime_id"] == "plugin:demo:invocation-1"


def test_disabled_plugin_cannot_execute(tmp_path):
    manager = manager_for(tmp_path, ["tool:echo"])
    manager.disable("demo")
    gateway = PluginExecutionGateway(manager, Registry(), RuntimeDispatcher())
    with pytest.raises(PluginError, match="not enabled"):
        gateway.execute("demo", "echo", {"text": "hello"}, invocation())
