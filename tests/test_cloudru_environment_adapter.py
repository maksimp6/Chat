from unittest.mock import Mock

import pytest

from cloud.base import CloudProviderError
from cloud.cloudru.environment_adapter import CloudRuEnvironmentAdapter
from cloud.cloudru.provider import CloudRuProvider


class FakeEnvironmentClient:
    """Mocked Cloud.ru API: never issues real HTTP calls."""

    def __init__(self):
        self.calls = []

    def request(self, service, method, path, **kwargs):
        self.calls.append((service, method, path, kwargs))
        if method == "POST" and path.endswith("/exec"):
            return {"success": True, "stdout": "hello\n", "stderr": "", "exit_code": 0}
        if method == "POST" and path == "/v1/sandboxes":
            return {"id": "sandbox-abc", "status": "provisioning"}
        return {"status": "ok"}


def _configure(monkeypatch):
    monkeypatch.setenv("CLOUDRU_ENVIRONMENT_ENDPOINT", "https://environment.example")
    monkeypatch.setenv("CLOUDRU_ENVIRONMENT_PATH", "/v1/sandboxes")


def test_provider_environment_lifecycle_uses_configured_path(monkeypatch):
    _configure(monkeypatch)
    client = FakeEnvironmentClient()
    provider = CloudRuProvider(client=client)

    created = provider.environment_create(
        name="env-1", commit_sha="a" * 40, branch="master", ttl_seconds=3600
    )
    assert created["resource"]["id"] == "sandbox-abc"

    provider.environment_start(remote_id="sandbox-abc")
    executed = provider.environment_exec(remote_id="sandbox-abc", command="echo hi")
    assert executed["result"]["stdout"] == "hello\n"
    provider.environment_stop(remote_id="sandbox-abc")
    provider.environment_delete(remote_id="sandbox-abc")

    paths = [call[2] for call in client.calls]
    assert "/v1/sandboxes" in paths
    assert "/v1/sandboxes/sandbox-abc/start" in paths
    assert "/v1/sandboxes/sandbox-abc/exec" in paths
    assert "/v1/sandboxes/sandbox-abc/stop" in paths
    assert "/v1/sandboxes/sandbox-abc" in paths
    delete_call = next(call for call in client.calls if call[1] == "DELETE")
    assert delete_call[2] == "/v1/sandboxes/sandbox-abc"


def test_provider_environment_methods_require_configuration(monkeypatch):
    monkeypatch.delenv("CLOUDRU_ENVIRONMENT_ENDPOINT", raising=False)
    monkeypatch.delenv("CLOUDRU_ENVIRONMENT_PATH", raising=False)
    provider = CloudRuProvider(client=FakeEnvironmentClient())
    with pytest.raises(CloudProviderError, match="not configured"):
        provider.environment_create(name="env-1", commit_sha="a" * 40)


def test_provider_environment_methods_validate_remote_id(monkeypatch):
    _configure(monkeypatch)
    provider = CloudRuProvider(client=FakeEnvironmentClient())
    with pytest.raises(CloudProviderError, match="remote_id is required"):
        provider.environment_start(remote_id="")
    with pytest.raises(CloudProviderError, match="remote_id is required"):
        provider.environment_exec(remote_id="", command="echo hi")
    with pytest.raises(CloudProviderError, match="command is required"):
        provider.environment_exec(remote_id="sandbox-abc", command="")
    with pytest.raises(CloudProviderError, match="remote_id is required"):
        provider.environment_stop(remote_id="")
    with pytest.raises(CloudProviderError, match="remote_id is required"):
        provider.environment_delete(remote_id="")


def test_capabilities_report_environment_service_state(monkeypatch):
    monkeypatch.delenv("CLOUDRU_ENVIRONMENT_ENDPOINT", raising=False)
    monkeypatch.delenv("CLOUDRU_ENVIRONMENT_PATH", raising=False)
    provider = CloudRuProvider(client=FakeEnvironmentClient())
    assert provider.capabilities()["services"]["environment"]["enabled"] is False
    _configure(monkeypatch)
    assert provider.capabilities()["services"]["environment"]["enabled"] is True


def test_adapter_provision_persists_remote_id_and_execute_stop_remove():
    provider = Mock(spec=CloudRuProvider)
    provider.environment_create.return_value = {"resource": {"id": "sandbox-xyz"}}
    provider.environment_exec.return_value = {
        "result": {"success": True, "stdout": "ok", "stderr": "", "exit_code": 0}
    }

    environment = {
        "environment_id": "env-1",
        "commit_sha": "a" * 40,
        "branch_name": "master",
        "ttl_seconds": 3600,
    }
    adapter = CloudRuEnvironmentAdapter(environment, provider=provider)

    provisioned = adapter.provision()
    assert provisioned == {"cloud_resource_id": "sandbox-xyz"}
    environment.update(provisioned)

    adapter.start()
    provider.environment_start.assert_called_once_with(remote_id="sandbox-xyz")

    result = adapter.execute("echo ok", timeout_seconds=5)
    assert result == {"success": True, "stdout": "ok", "stderr": "", "exit_code": 0}

    adapter.stop()
    provider.environment_stop.assert_called_once_with(remote_id="sandbox-xyz")

    adapter.remove()
    provider.environment_delete.assert_called_once_with(remote_id="sandbox-xyz")


def test_adapter_provision_raises_when_provider_returns_no_id():
    provider = Mock(spec=CloudRuProvider)
    provider.environment_create.return_value = {"resource": {}}
    adapter = CloudRuEnvironmentAdapter(
        {"environment_id": "env-1", "commit_sha": "a" * 40}, provider=provider
    )
    with pytest.raises(CloudProviderError, match="did not return an environment id"):
        adapter.provision()


def test_adapter_resolves_default_provider_from_registry(monkeypatch):
    provider = Mock(spec=CloudRuProvider)
    provider.environment_start.return_value = {"result": "ok"}
    registry = Mock()
    registry.get.return_value = provider
    monkeypatch.setattr(
        "cloud.cloudru.environment_adapter.ensure_default_providers", lambda: registry
    )

    adapter = CloudRuEnvironmentAdapter({"environment_id": "env-1", "cloud_resource_id": "sandbox-1"})
    adapter.start()

    registry.get.assert_called_once_with("cloudru")
    provider.environment_start.assert_called_once_with(remote_id="sandbox-1")


def test_adapter_operations_without_provisioned_resource_are_safe_noops():
    provider = Mock(spec=CloudRuProvider)
    adapter = CloudRuEnvironmentAdapter({"environment_id": "env-1"}, provider=provider)

    adapter.stop()
    adapter.remove()
    provider.environment_stop.assert_not_called()
    provider.environment_delete.assert_not_called()

    with pytest.raises(CloudProviderError, match="has not been provisioned"):
        adapter.execute("echo hi")
