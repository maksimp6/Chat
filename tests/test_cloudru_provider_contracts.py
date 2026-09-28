from unittest.mock import Mock, patch

import pytest
from cloud.base import CloudProviderError
from cloud.cloudru.client import CloudRuClient
from cloud.cloudru.provider import CloudRuProvider
from cloud.policy import tool_requires_confirmation
from cloud.registry import (
    CloudProviderRegistry,
    ensure_default_providers,
    get_registry,
    resolve_provider_name,
)
from cloud.tools import (
    cloud_capabilities,
    cloud_costs_summary,
    cloud_logs_query,
    cloud_metrics_query,
    cloud_resources_get,
    cloud_resources_list,
)


class FakeCloudClient:
    def request(self, service, method, path, **kwargs):
        if service == "observability" and "logs" in path:
            return {"logs": [{"message": "ok"}]}
        if service == "observability" and "metrics" in path:
            return {"series": [{"name": "cpu"}]}
        if service == "billing":
            return {"summary": {"total": 12}}
        if service == "backup":
            return {"items": [{"id": "backup-1"}]}
        if method == "GET" and path.count("/") > 2:
            return {"id": "resource-1", "name": "resource"}
        return {"items": [{"id": "resource-1", "name": "resource"}]}


class FakeProvider:
    name = "fake"

    def capabilities(self):
        return {"provider": "fake"}

    def list_resources(self, **kwargs):
        return {"resources": [], "kwargs": kwargs}

    def get_resource(self, **kwargs):
        return {"resource": kwargs}

    def query_logs(self, **kwargs):
        return {"logs": kwargs}

    def query_metrics(self, **kwargs):
        return {"metrics": kwargs}

    def costs_summary(self, **kwargs):
        return {"costs": kwargs}


def _configure_services(monkeypatch):
    for service in (
        "compute",
        "storage",
        "network",
        "database",
        "kubernetes",
        "security",
        "backup",
        "billing",
    ):
        upper = service.upper()
        monkeypatch.setenv(f"CLOUDRU_{upper}_ENDPOINT", f"https://{service}.example")
        monkeypatch.setenv(f"CLOUDRU_{upper}_PATH", f"/v1/{service}")
    monkeypatch.setenv("CLOUDRU_OBSERVABILITY_ENDPOINT", "https://observability.example")
    monkeypatch.setenv("CLOUDRU_OBSERVABILITY_LOGS_PATH", "/v1/logs")
    monkeypatch.setenv("CLOUDRU_OBSERVABILITY_METRICS_PATH", "/v1/metrics")


def test_provider_capabilities_and_all_configured_read_write_operations(monkeypatch):
    _configure_services(monkeypatch)
    provider = CloudRuProvider(client=FakeCloudClient())

    capabilities = provider.capabilities()
    assert all(
        capabilities["services"][service]["enabled"]
        for service in ("compute", "storage", "network", "database", "kubernetes", "security")
    )
    assert provider.list_resources(service="storage")["raw_count"] == 1
    assert provider.get_resource(resource_type="storage", resource_id="resource-1")["resource"]["id"]
    assert provider.compute(operation="list")["raw_count"] == 1
    assert provider.compute(operation="status", instance_id="resource-1")
    for operation in ("start", "stop", "reboot"):
        assert provider.compute(operation=operation, instance_id="resource-1")["operation"] == operation
    assert provider.query_logs(query="error", limit=0)["entries"]
    assert provider.query_metrics(query="cpu", limit=1001)["series"]
    for operation in ("create", "list", "restore", "delete"):
        assert provider.backup(operation=operation)["operation"] == operation
    assert provider.costs_summary(period="today", group_by="service")["summary"]


def test_provider_validation_and_bad_payloads(monkeypatch):
    _configure_services(monkeypatch)
    provider = CloudRuProvider(client=FakeCloudClient())
    with pytest.raises(CloudProviderError, match="service is required"):
        provider.list_resources(service="")
    with pytest.raises(CloudProviderError, match="resource_id is required"):
        provider.get_resource(resource_type="storage", resource_id="")
    with pytest.raises(CloudProviderError, match="instance_id is required"):
        provider.compute(operation="status")
    with pytest.raises(CloudProviderError, match="Unsupported compute operation"):
        provider.compute(operation="unknown")
    with pytest.raises(CloudProviderError, match="Unsupported backup operation"):
        provider.backup(operation="unknown")
    with pytest.raises(CloudProviderError, match="not configured"):
        provider.list_resources(service="unknown")

    class BadClient:
        def request(self, *args, **kwargs):
            return {"items": ["not-an-object"]}

    with pytest.raises(CloudProviderError, match="non-object"):
        CloudRuProvider(client=BadClient()).list_resources(service="storage")


def test_provider_action_path_validation(monkeypatch):
    _configure_services(monkeypatch)
    provider = CloudRuProvider(client=FakeCloudClient())
    monkeypatch.setenv("CLOUDRU_COMPUTE_ACTION_PATH", "{missing}")
    with pytest.raises(CloudProviderError, match="template is invalid"):
        provider.compute(operation="start", instance_id="vm-1")
    monkeypatch.setenv("CLOUDRU_COMPUTE_ACTION_PATH", "relative/{instance_id}")
    with pytest.raises(CloudProviderError, match="absolute path"):
        provider.compute(operation="start", instance_id="vm-1")


def test_provider_iam_filters_and_lookup_errors():
    iam = Mock()
    iam.list_service_accounts.return_value = [{"id": "sa-1"}]
    iam.list_api_keys.return_value = [{"id": "key-1"}]
    provider = CloudRuProvider(iam_client=iam)
    assert provider.list_resources(service="iam", resource_type="service_account")
    assert provider.list_resources(
        service="iam",
        resource_type="api_keys",
        filters={"service_account_id": "sa-1", "enabled": True},
    )
    with pytest.raises(CloudProviderError, match="service_account_id"):
        provider.list_resources(service="iam", resource_type="api_key")
    with pytest.raises(CloudProviderError, match="boolean"):
        provider.list_resources(
            service="iam",
            resource_type="api_key",
            filters={"service_account_id": "sa-1", "enabled": "maybe"},
        )
    with pytest.raises(CloudProviderError, match="not found"):
        provider.get_resource(service="iam", resource_type="service_account", resource_id="missing")
    with pytest.raises(CloudProviderError, match="unsupported"):
        provider.get_resource(service="iam", resource_type="api_key", resource_id="key-1")


def test_cloud_tool_read_wrappers_and_policy():
    provider = FakeProvider()
    with patch("cloud.tools._provider", return_value=provider):
        assert cloud_capabilities({"provider": "fake"})["provider"] == "fake"
        assert cloud_resources_list(
            {"provider": "fake", "service": "storage", "resource_type": None, "filters": None}
        )
        assert cloud_resources_get(
            {
                "provider": "fake",
                "service": None,
                "resource_type": "storage",
                "resource_id": "resource-1",
            }
        )
        assert cloud_logs_query(
            {"provider": "fake", "query": "error", "service": None, "limit": None}
        )
        assert cloud_metrics_query(
            {"provider": "fake", "query": "cpu", "service": None, "window": None, "limit": None}
        )
        assert cloud_costs_summary({"provider": "fake", "period": None, "group_by": None})
    assert tool_requires_confirmation("cloud.compute.stop")
    assert not tool_requires_confirmation("cloud.resources.list")


def test_cloud_registry_and_provider_resolution(monkeypatch):
    registry = CloudProviderRegistry()
    with pytest.raises(CloudProviderError, match="Unknown cloud provider"):
        registry.get("missing")
    registry.register(FakeProvider())
    assert registry.names() == ["fake"]
    assert registry.get("fake").name == "fake"
    monkeypatch.delenv("ALICE_CLOUD_PROVIDER", raising=False)
    assert resolve_provider_name({}) == "cloudru"
    assert resolve_provider_name({"provider": " Fake "}) == "fake"
    assert get_registry() is ensure_default_providers()
    assert "cloudru" in get_registry().names()


def test_client_auth_fallback_and_response_validation(monkeypatch):
    monkeypatch.delenv("CLOUDRU_COMPUTE_ENDPOINT", raising=False)
    assert CloudRuClient(api_key="token").endpoint("iam") == "https://iam.api.cloud.ru"
    with pytest.raises(CloudProviderError, match="not configured"):
        CloudRuClient(api_key="token").endpoint("unknown")

    iam = Mock()
    iam._token.return_value = "iam-token"
    client = CloudRuClient(iam_client=iam)
    assert client._auth_header() == ("Bearer", "iam-token")
    with pytest.raises(CloudProviderError, match="authentication"):
        CloudRuClient(api_key=None, iam_client=Mock(key_id=None, key_secret=None))._auth_header()

    monkeypatch.setenv("CLOUDRU_COMPUTE_ENDPOINT", "https://compute.example")
    bad_response = Mock(content=b"not-json", status_code=200)
    bad_response.json.side_effect = ValueError()
    with patch("cloud.cloudru.client.requests.request", return_value=bad_response):
        with pytest.raises(CloudProviderError, match="invalid JSON"):
            CloudRuClient(api_key="token").request("compute", "GET", "/v1/instances")

    object_response = Mock(content=b"[]", status_code=200)
    object_response.json.return_value = []
    with patch("cloud.cloudru.client.requests.request", return_value=object_response):
        with pytest.raises(CloudProviderError, match="non-object"):
            CloudRuClient(api_key="token").request("compute", "GET", "/v1/instances")
