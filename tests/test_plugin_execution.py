import json

import pytest

from invocation_context import InvocationContext
from plugin_execution import PluginExecutionGateway
from plugin_manager import PluginError, PluginManager
from universal_tool_platform import UniversalToolDefinition


def write_plugin(root, **overrides):
    folder = root / "demo"
    folder.mkdir()
    manifest = {
        "api_version": 1, "id": "demo", "name": "Demo Plugin", "version": "1.0.0",
        "capabilities": ["read"], "permissions": ["tool:demo_read"],
    }
    manifest.update(overrides)
    (folder / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")


class Registry:
    def __init__(self, capabilities=("read",)):
        self.capabilities = capabilities
        self.calls = []

    def get_universal_definition(self, name):
        if name != "demo_read":
            return None
        return UniversalToolDefinition(
            name=name, description="read demo data", input_schema={"type": "object"},
            output_schema={"type": "object"}, capabilities=self.capabilities,
            read_only=True, requires_approval=False,
        )

    def execute(self, name, arguments, context):
        self.calls.append((name, arguments, context))
        return {"value": arguments["value"]}


def context():
    return InvocationContext(
        session_id="session", conversation_id="conversation",
        invocation_id="invocation", trace_id="trace", user_id="user"
    )


def enabled_gateway(tmp_path, **manifest):
    write_plugin(tmp_path, **manifest)
    manager = PluginManager(tmp_path)
    manager.discover()
    manager.enable("demo")
    registry = Registry()
    return manager, registry, PluginExecutionGateway(manager, registry)


def test_successful_scoped_execution_preserves_correlation(tmp_path):
    _, registry, gateway = enabled_gateway(tmp_path)
    result = gateway.execute("demo", "demo_read", {"value": 7}, context())
    assert result["success"] is True
    assert result["data"] == {"value": 7}
    assert result["metadata"]["plugin_id"] == "demo"
    assert result["metadata"]["trace_id"] == "trace"
    assert result["metadata"]["invocation_id"] == "invocation"
    assert result["metadata"]["runtime_id"] == "plugin:demo:invocation"
    assert registry.calls[0][2]["call"].metadata["plugin_id"] == "demo"


def test_permission_denial_does_not_reach_registry(tmp_path):
    _, registry, gateway = enabled_gateway(tmp_path, permissions=["tool:other"])
    result = gateway.execute("demo", "demo_read", {}, context())
    assert result["success"] is False
    assert result["metadata"]["phase"] == "authorization"
    assert "permission denied" in result["error"]
    assert registry.calls == []


def test_capability_denial_does_not_reach_registry(tmp_path):
    manager, registry, gateway = enabled_gateway(tmp_path, capabilities=["write"])
    result = gateway.execute("demo", "demo_read", {}, context())
    assert result["success"] is False
    assert "capability denied: read" in result["error"]
    assert registry.calls == []


def test_disabled_plugin_cannot_execute(tmp_path):
    manager, _, gateway = enabled_gateway(tmp_path)
    manager.disable("demo")
    with pytest.raises(PluginError, match="not enabled"):
        gateway.execute("demo", "demo_read", {}, context())
