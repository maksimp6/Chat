from unittest.mock import patch

import pytest

from cloud.policy import CONFIRMATION_REQUIRED_TOOLS
from cloud.base import CloudProviderError
from cloud.tools import (
    CLOUD_TOOLS,
    cloud_backup,
    cloud_compute,
    cloud_environment_create,
    cloud_environment_delete,
    cloud_environment_exec,
    cloud_environment_start,
    cloud_environment_stop,
    cloud_ssh_exec,
)


class FakeProvider:
    name = "fake"

    def capabilities(self):
        return {"provider": "fake", "services": {}}

    def list_resources(self, **kwargs):
        return {"provider": "fake", "kwargs": kwargs}

    def get_resource(self, **kwargs):
        return {"provider": "fake", "resource": kwargs}

    def compute(self, **kwargs):
        return {"provider": "fake", "compute": kwargs}

    def query_logs(self, **kwargs):
        return {"provider": "fake", "logs": kwargs}

    def query_metrics(self, **kwargs):
        return {"provider": "fake", "metrics": kwargs}

    def backup(self, **kwargs):
        return {"provider": "fake", "backup": kwargs}

    def costs_summary(self, **kwargs):
        return {"provider": "fake", "costs": kwargs}


def _provider_registry(provider):
    class Registry:
        def get(self, name):
            assert name == "fake"
            return provider

    return Registry()


def test_cloud_compute_dispatches_to_provider_registry():
    provider = FakeProvider()
    with (
        patch("cloud.tools.resolve_provider_name", return_value="fake"),
        patch("cloud.tools.ensure_default_providers", return_value=_provider_registry(provider)),
    ):
        result = cloud_compute(
            {
                "provider": "fake",
                "operation": "start",
                "instance_id": "vm-1",
                "extra": {"force": True},
            },
            {},
        )
    assert result["compute"]["operation"] == "start"
    assert result["compute"]["instance_id"] == "vm-1"


def test_cloud_tool_error_is_normalized():
    class FailingProvider(FakeProvider):
        def backup(self, **kwargs):
            raise CloudProviderError("denied", code="authorization_failed", http_status=403)

    provider = FailingProvider()
    with (
        patch("cloud.tools.resolve_provider_name", return_value="fake"),
        patch("cloud.tools.ensure_default_providers", return_value=_provider_registry(provider)),
    ):
        result = cloud_backup({"provider": "fake", "operation": "create"}, {})

    assert result["success"] is False
    assert result["metadata"]["provider_code"] == "authorization_failed"
    assert result["metadata"]["provider_http_status"] == 403


def test_unknown_provider_error_is_normalized():
    with patch("cloud.tools.resolve_provider_name", return_value="missing"):
        result = cloud_compute({"operation": "list"}, {})
    assert result["success"] is False
    assert result["metadata"]["provider_code"] == "unsupported_provider"


def test_cloud_ssh_exec_uses_runtime_tool_boundary():
    with patch(
        "cloud.tools.ssh_runtime_exec", return_value={"success": True, "stdout": "ok"}
    ) as ssh:
        result = cloud_ssh_exec(
            {"target": "prod-vm", "command": "uptime", "timeout_seconds": 20},
            {"_universal_context": {"call": object()}},
        )
    assert result["success"] is True
    ssh.assert_called_once()


def test_cloud_dangerous_tools_require_approval():
    assert CLOUD_TOOLS["cloud.compute.start"]["requires_approval"] is True
    assert CLOUD_TOOLS["cloud.compute.stop"]["requires_approval"] is True
    assert CLOUD_TOOLS["cloud.compute.reboot"]["requires_approval"] is True
    assert CLOUD_TOOLS["cloud.backup.create"]["requires_approval"] is True
    assert CLOUD_TOOLS["cloud.ssh.exec"]["requires_approval"] is True
    assert CLOUD_TOOLS["cloud.resources.list"]["requires_approval"] is False
    assert CLOUD_TOOLS["cloud.environment.create"]["requires_approval"] is True
    assert CLOUD_TOOLS["cloud.environment.start"]["requires_approval"] is True
    assert CLOUD_TOOLS["cloud.environment.stop"]["requires_approval"] is True
    assert CLOUD_TOOLS["cloud.environment.delete"]["requires_approval"] is True
    assert CLOUD_TOOLS["cloud.environment.exec"]["requires_approval"] is True
    for name in (
        "cloud.environment.create",
        "cloud.environment.start",
        "cloud.environment.stop",
        "cloud.environment.delete",
        "cloud.environment.exec",
    ):
        assert name in CONFIRMATION_REQUIRED_TOOLS


def test_cloud_environment_tools_delegate_to_environment_manager():
    with patch("cloud.tools.environment_manager") as manager:
        manager.create_environment.return_value = {"environment_id": "env-1", "status": "STOPPED"}
        result = cloud_environment_create(
            {"branch": "master", "commit_sha": None, "ttl_seconds": 900}, {}
        )
        manager.create_environment.assert_called_once_with(
            "master", None, None, adapter="cloudru", ttl_seconds=900
        )
        assert result["environment_id"] == "env-1"

        manager.start_environment.return_value = {"status": "RUNNING"}
        assert cloud_environment_start({"environment_id": "env-1"}, {})["status"] == "RUNNING"
        manager.start_environment.assert_called_once_with("env-1", None)

        manager.stop_environment.return_value = {"status": "STOPPED"}
        assert cloud_environment_stop({"environment_id": "env-1"}, {})["status"] == "STOPPED"

        manager.delete_environment.return_value = {"status": "DELETING"}
        assert cloud_environment_delete({"environment_id": "env-1"}, {})["status"] == "DELETING"

        manager.execute_environment.return_value = {"success": True, "stdout": "ok"}
        exec_result = cloud_environment_exec(
            {"environment_id": "env-1", "command": "echo ok", "timeout_seconds": 10}, {}
        )
        assert exec_result["success"] is True
        manager.execute_environment.assert_called_once_with(
            "env-1", "echo ok", None, timeout_seconds=10.0
        )


def test_cloud_environment_tools_normalize_errors():
    with patch("cloud.tools.environment_manager") as manager:
        manager.start_environment.side_effect = KeyError("environment_not_found")
        result = cloud_environment_start({"environment_id": "missing"}, {})
        assert result["success"] is False

        manager.create_environment.side_effect = CloudProviderError(
            "denied", code="authorization_failed", http_status=403
        )
        result = cloud_environment_create({"branch": "master"}, {})
        assert result["success"] is False

        manager.stop_environment.side_effect = ValueError("invalid environment transition")
        assert cloud_environment_stop({"environment_id": "env-1"}, {})["success"] is False

        manager.delete_environment.side_effect = KeyError("environment_not_found")
        assert cloud_environment_delete({"environment_id": "env-1"}, {})["success"] is False

        manager.execute_environment.side_effect = ValueError("environment is not running")
        assert cloud_environment_exec({"environment_id": "env-1", "command": "x"}, {})[
            "success"
        ] is False


def test_cloud_environment_owner_id_resolved_from_trusted_identity():
    call = type("Call", (), {"user_id": " alice "})()
    cfg = {"_universal_context": {"call": call}}
    with patch("cloud.tools.environment_manager") as manager:
        manager.create_environment.return_value = {"environment_id": "env-1"}
        cloud_environment_create({"branch": "master"}, cfg)
        manager.create_environment.assert_called_once_with(
            "master", None, "alice", adapter="cloudru", ttl_seconds=None
        )


def test_cloud_compute_schema_rejects_missing_instance_id():
    params = CLOUD_TOOLS["cloud.compute.start"]["parameters"]
    assert "instance_id" in params["required"]


@pytest.mark.parametrize(
    "name",
    [
        "cloud.capabilities",
        "cloud.resources.list",
        "cloud.resources.get",
        "cloud.compute.list",
        "cloud.compute.status",
        "cloud.logs.query",
        "cloud.metrics.query",
        "cloud.backup.create",
        "cloud.costs.summary",
        "cloud.ssh.exec",
    ],
)
def test_cloud_tools_registered_with_cloud_capability(name):
    assert "cloud" in CLOUD_TOOLS[name]["capabilities"]


def test_confirmation_policy_only_references_registered_tools():
    assert CONFIRMATION_REQUIRED_TOOLS.issubset(set(CLOUD_TOOLS.keys()))
