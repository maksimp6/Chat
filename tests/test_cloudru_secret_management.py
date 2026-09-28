import json
from unittest.mock import Mock, patch

import pytest
import requests

from cloud.base import CloudProviderError
from cloud.cloudru.secret_management import CloudRuSecretManagementClient
from trace_manager import ExecutionTrace


def _client(monkeypatch, **kwargs):
    monkeypatch.setenv("CLOUDRU_SECRET_MANAGEMENT_KEY_ID", "runtime-key-id")
    monkeypatch.setenv("CLOUDRU_SECRET_MANAGEMENT_KEY_SECRET", "runtime-key-secret")
    return CloudRuSecretManagementClient(**kwargs)


def _ok_response(payload):
    response = Mock()
    response.ok = True
    response.status_code = 200
    response.json.return_value = payload
    return response


def _error_response(status_code, payload=None):
    response = Mock()
    response.ok = False
    response.status_code = status_code
    response.json.return_value = payload or {}
    return response


def test_requires_runtime_credentials_not_admin_pair(monkeypatch):
    monkeypatch.delenv("CLOUDRU_SECRET_MANAGEMENT_KEY_ID", raising=False)
    monkeypatch.delenv("CLOUDRU_SECRET_MANAGEMENT_KEY_SECRET", raising=False)
    monkeypatch.setenv("CLOUDRU_IAM_KEY_ID", "admin-key-id")
    monkeypatch.setenv("CLOUDRU_IAM_KEY_SECRET", "admin-key-secret")

    client = CloudRuSecretManagementClient()
    with pytest.raises(CloudProviderError) as exc:
        client.get_secret_value("secret-1", "v1")
    assert exc.value.code == "auth_not_configured"


def test_get_secret_value_returns_plaintext_and_caches(monkeypatch):
    client = _client(monkeypatch, cache_ttl=30.0)
    with (
        patch.object(client.iam_client, "_token", return_value="iam-token"),
        patch(
            "cloud.cloudru.secret_management.requests.get",
            return_value=_ok_response({"value": "top-secret-value"}),
        ) as get,
    ):
        first = client.get_secret_value("secret-1", "v1")
        second = client.get_secret_value("secret-1", "v1")

    assert first == "top-secret-value"
    assert second == "top-secret-value"
    get.assert_called_once()
    assert "Bearer iam-token" == get.call_args.kwargs["headers"]["Authorization"]
    assert "v1" in get.call_args.args[0]


def test_new_pinned_version_bypasses_stale_cache(monkeypatch):
    client = _client(monkeypatch, cache_ttl=30.0)
    responses = [
        _ok_response({"value": "value-v1"}),
        _ok_response({"value": "value-v2"}),
    ]
    with (
        patch.object(client.iam_client, "_token", return_value="iam-token"),
        patch("cloud.cloudru.secret_management.requests.get", side_effect=responses),
    ):
        assert client.get_secret_value("secret-1", "v1") == "value-v1"
        assert client.get_secret_value("secret-1", "v2") == "value-v2"


@pytest.mark.parametrize(
    "status_code,expected_code",
    [(401, "auth_failed"), (403, "authorization_failed"), (404, "not_found"), (409, "version_disabled")],
)
def test_status_codes_map_to_predictable_error_codes(monkeypatch, status_code, expected_code):
    client = _client(monkeypatch)
    with (
        patch.object(client.iam_client, "_token", return_value="iam-token"),
        patch(
            "cloud.cloudru.secret_management.requests.get",
            return_value=_error_response(status_code),
        ) as get,
    ):
        with pytest.raises(CloudProviderError) as exc:
            client.get_secret_value("secret-1", "v1")

    assert exc.value.code == expected_code
    # Errors are not silently retried past the first denied/absent response.
    get.assert_called_once()


def test_network_errors_are_retried_up_to_max_retries(monkeypatch):
    client = _client(monkeypatch, max_retries=2, cache_ttl=0)
    with (
        patch.object(client.iam_client, "_token", return_value="iam-token"),
        patch(
            "cloud.cloudru.secret_management.requests.get",
            side_effect=requests.RequestException("offline"),
        ) as get,
    ):
        with pytest.raises(CloudProviderError) as exc:
            client.get_secret_value("secret-1", "v1")

    assert exc.value.code == "provider_unavailable"
    assert get.call_count == 3  # initial attempt + 2 retries


def test_disabled_cache_refetches_every_call(monkeypatch):
    client = _client(monkeypatch, cache_ttl=0)
    with (
        patch.object(client.iam_client, "_token", return_value="iam-token"),
        patch(
            "cloud.cloudru.secret_management.requests.get",
            return_value=_ok_response({"value": "v"}),
        ) as get,
    ):
        client.get_secret_value("secret-1", "v1")
        client.get_secret_value("secret-1", "v1")
    assert get.call_count == 2


def test_missing_value_in_payload_fails_closed(monkeypatch):
    client = _client(monkeypatch)
    with (
        patch.object(client.iam_client, "_token", return_value="iam-token"),
        patch(
            "cloud.cloudru.secret_management.requests.get",
            return_value=_ok_response({"unexpected": "shape"}),
        ),
    ):
        with pytest.raises(CloudProviderError) as exc:
            client.get_secret_value("secret-1", "v1")
    assert exc.value.code == "invalid_response"


def test_secret_value_never_reaches_trace_on_success(monkeypatch):
    client = _client(monkeypatch)
    trace = ExecutionTrace(trace_id="scsm-success-test")
    with (
        patch.object(client.iam_client, "_token", return_value="iam-token"),
        patch("cloud.cloudru.secret_management.get_current_trace", return_value=trace),
        patch(
            "cloud.cloudru.secret_management.requests.get",
            return_value=_ok_response({"value": "never-trace-me"}),
        ),
    ):
        value = client.get_secret_value("secret-1", "v1")

    assert value == "never-trace-me"
    snapshot = json.dumps(trace.make_snapshot(), ensure_ascii=False)
    assert "never-trace-me" not in snapshot


def test_secret_value_never_reaches_trace_or_exception_on_network_failure(monkeypatch):
    client = _client(monkeypatch, max_retries=0)
    trace = ExecutionTrace(trace_id="scsm-failure-test")
    with (
        patch.object(client.iam_client, "_token", return_value="iam-token"),
        patch("cloud.cloudru.secret_management.get_current_trace", return_value=trace),
        patch(
            "cloud.cloudru.secret_management.requests.get",
            side_effect=requests.RequestException("offline"),
        ),
        pytest.raises(CloudProviderError) as exc,
    ):
        client.get_secret_value("secret-1", "v1")

    snapshot = json.dumps(trace.make_snapshot(), ensure_ascii=False)
    assert "Authorization" not in snapshot
    assert "iam-token" not in snapshot
    assert exc.value.code == "provider_unavailable"


def test_error_response_body_never_leaks_into_exception_message(monkeypatch):
    client = _client(monkeypatch)
    with (
        patch.object(client.iam_client, "_token", return_value="iam-token"),
        patch(
            "cloud.cloudru.secret_management.requests.get",
            return_value=_error_response(500, {"message": "leaked-secret-echo"}),
        ),
    ):
        with pytest.raises(CloudProviderError) as exc:
            client.get_secret_value("secret-1", "v1")
    assert "leaked-secret-echo" not in str(exc.value)


def test_list_versions_reuses_shared_trace_safe_client(monkeypatch):
    client = _client(monkeypatch)
    with patch.object(
        client._client,
        "request",
        return_value={"versions": [{"id": "v1", "status": "enabled"}]},
    ) as request:
        versions = client.list_versions("secret-1")

    assert versions == [{"id": "v1", "status": "enabled"}]
    request.assert_called_once_with(
        "secret_management", "GET", "/v1/secrets/secret-1/versions"
    )


def test_get_version_status_reports_disabled_without_leaking_value(monkeypatch):
    client = _client(monkeypatch)
    with patch.object(
        client,
        "list_versions",
        return_value=[{"id": "v1", "status": "disabled"}],
    ):
        assert client.get_version_status("secret-1", "v1") == "disabled"

    with patch.object(client, "list_versions", return_value=[]):
        with pytest.raises(CloudProviderError) as exc:
            client.get_version_status("secret-1", "missing")
    assert exc.value.code == "not_found"
