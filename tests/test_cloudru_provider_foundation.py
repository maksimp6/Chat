from unittest.mock import Mock, patch

import pytest

from cloud.cloudru.client import CloudRuClient
from cloud.cloudru.provider import CloudRuProvider
from cloud.base import CloudProviderError
from tool_registry import ToolRegistry


class TraceRecorder:
    def __init__(self):
        self.events = []
        self.errors = []

    def add_event(self, event_type, payload):
        self.events.append((event_type, payload))

    def record_error(self, source, error, exception=None):
        self.errors.append((source, error, type(exception).__name__ if exception else None))


def test_cloudru_client_traces_without_leaking_authorization(monkeypatch):
    monkeypatch.setenv("CLOUDRU_API_KEY", "secret-token")
    monkeypatch.setenv("CLOUDRU_COMPUTE_ENDPOINT", "https://compute.example")
    trace = TraceRecorder()

    response = Mock()
    response.status_code = 200
    response.content = b'{"items":[]}'
    response.json.return_value = {"items": []}
    response.raise_for_status.return_value = None

    with (
        patch("cloud.cloudru.client.get_current_trace", return_value=trace),
        patch("cloud.cloudru.client.requests.request", return_value=response) as request,
    ):
        CloudRuClient().request("compute", "GET", "/v1/instances")

    headers = request.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Api-Key secret-token"
    payloads = [payload for _, payload in trace.events]
    assert all("secret-token" not in str(payload) for payload in payloads)
    assert any(payload.get("auth_scheme") == "Api-Key" for payload in payloads)


def test_cloudru_client_maps_http_error_to_provider_error(monkeypatch):
    monkeypatch.setenv("CLOUDRU_API_KEY", "secret-token")
    monkeypatch.setenv("CLOUDRU_COMPUTE_ENDPOINT", "https://compute.example")

    response = Mock(status_code=403)
    error = Exception("forbidden")
    http_error = __import__("requests").HTTPError("forbidden")
    http_error.response = response

    with patch("cloud.cloudru.client.requests.request", side_effect=http_error):
        with pytest.raises(CloudProviderError) as exc:
            CloudRuClient().request("compute", "GET", "/v1/instances")
    assert exc.value.code == "authorization_failed"
    assert exc.value.http_status == 403


def test_cloudru_provider_lists_iam_service_accounts():
    iam = Mock()
    iam.list_service_accounts.return_value = [{"id": "sa-1", "name": "Alice"}]

    provider = CloudRuProvider(iam_client=iam)
    result = provider.list_resources(service="iam")

    assert result["service"] == "iam"
    assert result["resources"][0]["id"] == "sa-1"


def test_cloudru_provider_lists_iam_api_keys_with_service_account_filter():
    iam = Mock()
    iam.list_api_keys.return_value = [{"id": "key-1", "service_account_id": "sa-1"}]
    provider = CloudRuProvider(iam_client=iam)

    result = provider.list_resources(
        service="iam",
        resource_type="api_key",
        filters={"service_account_id": "sa-1"},
    )

    assert result["resource_type"] == "api_key"
    assert result["resources"][0]["id"] == "key-1"
    iam.list_api_keys.assert_called_once_with(service_account_id="sa-1", enabled=None)


def test_cloudru_provider_parses_iam_api_keys_enabled_string_filter():
    iam = Mock()
    iam.list_api_keys.return_value = []
    provider = CloudRuProvider(iam_client=iam)
    provider.list_resources(
        service="iam",
        resource_type="api_key",
        filters={"service_account_id": "sa-1", "enabled": "false"},
    )
    iam.list_api_keys.assert_called_once_with(service_account_id="sa-1", enabled=False)


def test_cloudru_provider_get_iam_service_account_by_id():
    iam = Mock()
    iam.list_service_accounts.return_value = [{"id": "sa-1", "name": "Alice"}]
    provider = CloudRuProvider(iam_client=iam)
    result = provider.get_resource(
        service="iam", resource_type="service_account", resource_id="sa-1"
    )
    assert result["resource"]["id"] == "sa-1"


def test_cloudru_provider_get_iam_api_key_is_explicitly_unsupported():
    provider = CloudRuProvider(iam_client=Mock())
    with pytest.raises(CloudProviderError) as exc:
        provider.get_resource(service="iam", resource_type="api_key", resource_id="key-1")
    assert exc.value.code == "unsupported_operation"


def test_cloudru_provider_requires_config_for_compute_listing(monkeypatch):
    monkeypatch.delenv("CLOUDRU_COMPUTE_PATH", raising=False)
    provider = CloudRuProvider(client=Mock(spec=CloudRuClient))

    with pytest.raises(CloudProviderError) as exc:
        provider.compute(operation="list")
    assert exc.value.code == "unsupported_capability"


def test_cloudru_provider_non_iam_list_requires_endpoint_and_path(monkeypatch):
    monkeypatch.delenv("CLOUDRU_STORAGE_ENDPOINT", raising=False)
    monkeypatch.setenv("CLOUDRU_STORAGE_PATH", "/v1/resources")
    provider = CloudRuProvider(client=Mock(spec=CloudRuClient))
    with pytest.raises(CloudProviderError) as exc:
        provider.list_resources(service="storage")
    assert exc.value.code == "unsupported_capability"


def test_cloudru_provider_get_resource_requires_endpoint_and_path(monkeypatch):
    monkeypatch.delenv("CLOUDRU_STORAGE_ENDPOINT", raising=False)
    monkeypatch.setenv("CLOUDRU_STORAGE_PATH", "/v1/resources")
    provider = CloudRuProvider(client=Mock(spec=CloudRuClient))
    with pytest.raises(CloudProviderError) as exc:
        provider.get_resource(service="storage", resource_type="bucket", resource_id="r1")
    assert exc.value.code == "unsupported_capability"


def test_cloudru_provider_get_resource_uses_service_path(monkeypatch):
    monkeypatch.setenv("CLOUDRU_STORAGE_ENDPOINT", "https://storage.example")
    monkeypatch.setenv("CLOUDRU_STORAGE_PATH", "/v1/resources")
    client = Mock(spec=CloudRuClient)
    client.request.return_value = {"id": "r1", "name": "bucket"}
    provider = CloudRuProvider(client=client)
    result = provider.get_resource(service="storage", resource_type="bucket", resource_id="r1")
    assert result["resource"]["id"] == "r1"
    assert client.request.call_args.args == ("storage", "GET", "/v1/resources/r1")


@pytest.mark.parametrize(
    "call",
    [
        lambda provider: provider.query_logs(query="status"),
        lambda provider: provider.query_metrics(query="cpu"),
        lambda provider: provider.backup(operation="list"),
        lambda provider: provider.costs_summary(),
    ],
)
def test_cloudru_service_specific_calls_require_endpoint_and_path(monkeypatch, call):
    for key in [
        "CLOUDRU_OBSERVABILITY_ENDPOINT",
        "CLOUDRU_OBSERVABILITY_LOGS_PATH",
        "CLOUDRU_OBSERVABILITY_METRICS_PATH",
        "CLOUDRU_BACKUP_ENDPOINT",
        "CLOUDRU_BACKUP_PATH",
        "CLOUDRU_BILLING_ENDPOINT",
        "CLOUDRU_BILLING_SUMMARY_PATH",
    ]:
        monkeypatch.delenv(key, raising=False)
    provider = CloudRuProvider(client=Mock(spec=CloudRuClient))
    with pytest.raises(CloudProviderError) as exc:
        call(provider)
    assert exc.value.code == "unsupported_capability"


def test_cloudru_provider_rejects_mixed_resource_payload_types(monkeypatch):
    monkeypatch.setenv("CLOUDRU_STORAGE_ENDPOINT", "https://storage.example")
    monkeypatch.setenv("CLOUDRU_STORAGE_PATH", "/v1/resources")
    client = Mock(spec=CloudRuClient)
    client.request.return_value = {"items": [{"id": "ok"}, "bad-entry"]}
    provider = CloudRuProvider(client=client)

    with pytest.raises(CloudProviderError) as exc:
        provider.list_resources(service="storage")
    assert exc.value.code == "invalid_response"


def test_cloudru_compute_action_path_template_interpolates_instance_and_operation(monkeypatch):
    monkeypatch.setenv("CLOUDRU_COMPUTE_PATH", "/v1/instances")
    monkeypatch.setenv("CLOUDRU_COMPUTE_ACTION_PATH", "/v1/instances/{instance_id}/{operation}")
    client = Mock(spec=CloudRuClient)
    client.request.return_value = {"status": "queued"}
    provider = CloudRuProvider(client=client)

    provider.compute(operation="reboot", instance_id="vm-42")

    assert client.request.call_args.args[2] == "/v1/instances/vm-42/reboot"


def test_tool_registry_loads_cloud_category():
    registry = ToolRegistry()
    categories = registry.get_available_categories()
    assert "cloud" in categories
    assert "cloud.capabilities" in categories["cloud"]
