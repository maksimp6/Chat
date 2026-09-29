import json
from unittest.mock import Mock, patch

import pytest
import requests

from cloud.base import CloudProviderError
from cloud.cloudru.secret_management import CloudRuSecretManagementClient
from cloudru_iam import CloudRuIamError
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
    [
        (401, "auth_failed"),
        (403, "authorization_failed"),
        (404, "not_found"),
        (409, "version_disabled"),
    ],
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
    request.assert_called_once_with("secret_management", "GET", "/v1/secrets/secret-1/versions")


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


def test_explicit_iam_client_is_used_without_env_lookup(monkeypatch):
    monkeypatch.delenv("CLOUDRU_SECRET_MANAGEMENT_KEY_ID", raising=False)
    monkeypatch.delenv("CLOUDRU_SECRET_MANAGEMENT_KEY_SECRET", raising=False)
    iam = Mock(key_id="explicit-id", key_secret="explicit-secret")
    client = CloudRuSecretManagementClient(iam_client=iam)
    assert client.iam_client is iam


def test_invalidate_cache_can_clear_one_secret_or_all(monkeypatch):
    client = _client(monkeypatch)
    client._value_cache = {
        ("secret-1", "v1"): (1.0, "one"),
        ("secret-1", "v2"): (1.0, "two"),
        ("secret-2", "v1"): (1.0, "other"),
    }

    client.invalidate_cache("secret-1")
    assert set(client._value_cache) == {("secret-2", "v1")}

    client.invalidate_cache()
    assert client._value_cache == {}


@pytest.mark.parametrize(
    "secret_id,version_id",
    [("", "v1"), ("secret-1", ""), ("  ", "v1")],
)
def test_get_secret_value_rejects_missing_identifiers(monkeypatch, secret_id, version_id):
    client = _client(monkeypatch)
    with pytest.raises(ValueError, match="secret_id and version_id are required"):
        client.get_secret_value(secret_id, version_id)


def test_list_versions_validates_secret_id_and_payload(monkeypatch):
    client = _client(monkeypatch)
    with pytest.raises(ValueError, match="secret_id is required"):
        client.list_versions(" ")

    with patch.object(client._client, "request", return_value={"items": ["not-an-object"]}):
        with pytest.raises(CloudProviderError) as exc:
            client.list_versions("secret-1")
    assert exc.value.code == "invalid_response"


def test_version_status_accepts_alternate_metadata_names(monkeypatch):
    client = _client(monkeypatch)
    with patch.object(
        client,
        "list_versions",
        return_value=[{"version_id": "v2", "state": "active"}, {"id": "v3"}],
    ):
        assert client.get_version_status("secret-1", "v2") == "active"
        assert client.get_version_status("secret-1", "v3") == "unknown"


def test_iam_token_failure_is_normalized(monkeypatch):
    client = _client(monkeypatch)
    with patch.object(client.iam_client, "_token", side_effect=CloudRuIamError("denied")):
        with pytest.raises(CloudProviderError) as exc:
            client.get_secret_value("secret-1", "v1")
    assert exc.value.code == "auth_failed"


def test_trace_helper_is_noop_without_trace():
    CloudRuSecretManagementClient._trace_value_response(
        None, secret_id="secret-1", started=0.0, status=200, success=True
    )


@pytest.mark.parametrize("payload", [None, ["not", "an", "object"]])
def test_value_response_rejects_invalid_json_shapes(payload):
    response = _ok_response({"value": "placeholder"})
    if payload is None:
        response.json.side_effect = ValueError("invalid json")
    else:
        response.json.return_value = payload

    with pytest.raises(CloudProviderError) as exc:
        CloudRuSecretManagementClient._parse_value_response(response, secret_id="secret-1")
    assert exc.value.code == "invalid_response"


def test_uses_official_secret_manager_endpoint_and_payload_route(monkeypatch):
    client = _client(monkeypatch, cache_ttl=0)
    with (
        patch.object(client.iam_client, "_token", return_value="iam-token"),
        patch(
            "cloud.cloudru.secret_management.requests.get",
            return_value=_ok_response({"value": "secret-value"}),
        ) as get,
    ):
        assert client.get_secret_value("secret-1", "v7") == "secret-value"

    assert get.call_args.args[0] == (
        "https://secretmanager.api.cloud.ru/v1/secrets/secret-1/versions/v7/payload"
    )


def test_cache_sweeps_expired_entries_and_enforces_bound(monkeypatch):
    client = _client(monkeypatch, cache_ttl=10.0, cache_max_entries=2)
    client._value_cache = {
        ("expired", "v1"): (1.0, "old-secret"),
        ("keep", "v1"): (95.0, "keep-secret"),
        ("also-keep", "v1"): (96.0, "also-secret"),
    }

    with patch("cloud.cloudru.secret_management.time.monotonic", return_value=100.0):
        client._sweep_cache()

    assert ("expired", "v1") not in client._value_cache
    assert len(client._value_cache) <= 2

    client._value_cache[("newer", "v1")] = (97.0, "newer-secret")
    with patch("cloud.cloudru.secret_management.time.monotonic", return_value=100.0):
        client._sweep_cache()

    assert len(client._value_cache) == 2
    assert ("keep", "v1") not in client._value_cache


def test_zero_ttl_sweep_clears_plaintext_cache(monkeypatch):
    client = _client(monkeypatch, cache_ttl=0)
    client._value_cache[("secret-1", "v1")] = (1.0, "plaintext")
    client._sweep_cache(2.0)
    assert client._value_cache == {}
