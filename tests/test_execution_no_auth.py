"""Shared execution keeps configuration and transport checks without application grants."""

import json
from types import SimpleNamespace

import pytest

from agent_runtime.gateway import AgentDescriptor, AgentGateway, AgentInvocationError
from invocation.context import InvocationContext
from plugin_system.execution import PluginExecutionGateway
from plugin_system.manager import PluginError, PluginManager
from remote_runtime.ssh import SSHRuntime, SSHRuntimeError
import remote_runtime.ssh_settings as ssh_settings
from runtime import RuntimeDispatcher
from tool_providers.universal_tool_platform import UniversalToolCall, UniversalToolDefinition, UniversalToolExecutor


class Registry:
    def __init__(self):
        self.calls = []

    def get_universal_definition(self, name):
        return UniversalToolDefinition(
            name=name,
            description="fixture",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            output_schema={"type": "object"},
            requires_approval=True,
            capabilities=("files.write",),
        )

    def execute(self, name, arguments, **kwargs):
        self.calls.append((name, arguments))
        return {"echo": arguments["text"]}


def test_universal_executor_does_not_consult_legacy_access_hooks():
    registry = Registry()
    hooks = []
    def reject(*args):
        hooks.append(args)
        raise PermissionError("legacy denied")
    result = UniversalToolExecutor(registry).execute(
        UniversalToolCall("echo", {"text": "hello"}),
        authorize=reject, policy=reject, approval=reject,
    )
    assert result["success"] is True
    assert result["data"] == {"echo": "hello"}
    assert hooks == []
    invalid = UniversalToolExecutor(registry).execute(UniversalToolCall("echo", {}))
    assert invalid["success"] is False
    assert invalid["metadata"]["phase"] == "validation"


def test_plugin_runs_without_manifest_grants_and_keeps_enable_setting(tmp_path):
    folder = tmp_path / "demo"
    folder.mkdir()
    (folder / "plugin.json").write_text(json.dumps({"api_version": 1, "id": "demo", "name": "Demo", "version": "1.0.0", "permissions": [], "capabilities": []}))
    manager = PluginManager(tmp_path)
    manager.discover()
    manager.enable("demo")
    registry = Registry()
    gateway = PluginExecutionGateway(manager, registry, RuntimeDispatcher())
    context = InvocationContext.create("session", "conversation")
    result = gateway.execute("demo", "echo", {"text": "hello"}, context)
    assert result["success"] is True
    assert registry.calls == [("echo", {"text": "hello"})]
    manager.disable("demo")
    with pytest.raises(PluginError, match="not enabled"):
        gateway.execute("demo", "echo", {"text": "hello"}, context)


def test_agent_runs_without_user_or_approval_but_keeps_enabled_setting():
    gateway = AgentGateway()
    gateway.register(AgentDescriptor("restricted", "fixture", allowed_users=("old-owner",), requires_approval=True), lambda payload: dict(payload))
    result = gateway.invoke("restricted", {"value": 5})
    assert result.status == "completed"
    assert result.result == {"value": 5}
    gateway.register(AgentDescriptor("disabled", "fixture", enabled=False), lambda payload: payload)
    with pytest.raises(AgentInvocationError, match="disabled"):
        gateway.invoke("disabled", {})


@pytest.mark.parametrize("identity", [None, "unmapped", "legacy"])
def test_ssh_uses_default_os_user_without_logos_identity_rejection(identity, monkeypatch):
    runtime = SSHRuntime(targets={"fixture": {"host": "example.invalid", "default_user": "configured", "identity_map": {"legacy": "old-user"}, "allowed_users": ["old-user"], "known_hosts": "/fixture/known_hosts"}})
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="fixture", stderr="")
    monkeypatch.setattr("remote_runtime.ssh.subprocess.run", run)
    result = runtime.execute(target="fixture", command="printf fixture", identity_id=identity)
    assert result["linux_user"] == "configured"
    assert "configured@example.invalid" in calls[0]
    assert "StrictHostKeyChecking=yes" in calls[0]


def test_ssh_uses_first_os_mapping_when_no_default_is_configured():
    runtime = SSHRuntime(targets={"fixture": {"host": "example.invalid", "identity_map": {"legacy": "first", "another": "second"}}})
    target, user = runtime._resolve_target("fixture")
    assert user == "first"
    with pytest.raises(SSHRuntimeError, match="host-key verification"):
        runtime._ssh_command(target, user, "true")


def test_ssh_write_no_longer_requires_application_approval(monkeypatch):
    settings = {**ssh_settings._DEFAULTS, "enabled": True}
    monkeypatch.setattr(ssh_settings, "get_settings", lambda: settings)
    ssh_settings.assert_operation_allowed("write_file", approved=False)
    settings["read_only"] = True
    with pytest.raises(SSHRuntimeError, match="read-only"):
        ssh_settings.assert_operation_allowed("write_file")
    settings["enabled"] = False
    with pytest.raises(SSHRuntimeError, match="disabled"):
        ssh_settings.assert_operation_allowed("read_file")


def test_ssh_settings_allow_command_execution_without_legacy_allowlist():
    settings = {**ssh_settings._DEFAULTS, "enabled": True, "known_hosts": "/fixture/known_hosts", "targets": {"fixture": {"host": "example.invalid", "default_user": "configured"}}, "command_allowlist": []}
    assert ssh_settings.validate_settings(settings)["allow_command_execution"] is True
