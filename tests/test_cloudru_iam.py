from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from cloudru_iam import CloudRuIamClient
from cloudru_iam_routes import _parse_expiry, _validate_ips


def test_cloudru_token_exchange_uses_service_account_key_pair():
    client = CloudRuIamClient(key_id="id", key_secret="secret")
    response = Mock()
    response.json.return_value = {"token": "iam-token", "expires_in": 3600}
    response.content = b"{}"
    response.raise_for_status.return_value = None

    with patch("cloudru_iam.requests.post", return_value=response) as post:
        assert client._token() == "iam-token"

    assert post.call_args.kwargs["json"] == {"keyId": "id", "secret": "secret"}


def test_cloudru_list_api_keys_uses_documented_filter():
    client = CloudRuIamClient(key_id="id", key_secret="secret")
    with patch.object(client, "_request", return_value={"keys": [{"id": "1"}]}) as req:
        keys = client.list_api_keys(service_account_id="sa-1", enabled=True)
    assert keys == [{"id": "1"}]
    assert req.call_args.kwargs["params"]["filter.service_account_id"] == "sa-1"
    assert req.call_args.kwargs["params"]["filter.enabled"] == "true"


def test_cloudru_create_builds_restricted_payload():
    client = CloudRuIamClient(key_id="id", key_secret="secret")
    with patch.object(client, "_request", return_value={"id": "key-1", "secret": "sek"}) as req:
        response = client.create_api_key(
            service_account_id="sa-1",
            name="Alice Pro",
            products=["monaas", "foundation-models"],
            expires_at="2026-10-01T00:00:00Z",
            ip_addresses=["192.0.2.0/24"],
            time_slots=[{"start": 9, "end": 18}],
            timezone_offset=3,
        )
    assert response["id"] == "key-1"
    body = req.call_args.kwargs["json_body"]
    assert body["service_account_id"] == "sa-1"
    assert body["products"] == ["monaas", "foundation-models"]
    assert body["restrictions"]["ip_addresses"]["ip_addresses"] == ["192.0.2.0/24"]
    assert body["restrictions"]["time_range"]["timezone"] == 3


def test_expiry_is_limited_to_cloudru_supported_window():
    now = datetime.now(timezone.utc)
    assert _parse_expiry((now + timedelta(days=30)).isoformat())
    try:
        _parse_expiry((now + timedelta(days=366)).isoformat())
    except ValueError:
        pass
    else:
        raise AssertionError("expiry above one year must fail")


def test_ip_validation_rejects_invalid_values():
    assert _validate_ips(["192.0.2.0/24"]) == ["192.0.2.0/24"]
    try:
        _validate_ips(["not-an-ip"])
    except ValueError:
        pass
    else:
        raise AssertionError("invalid IP should fail")


def test_cloudru_reissue_api_key_uses_same_resource_endpoint():
    client = CloudRuIamClient(key_id="id", key_secret="secret")
    with patch.object(client, "_request", return_value={"id": "key-1", "secret": "new-secret"}) as req:
        response = client.reissue_api_key(
            api_key_id="key-1",
            expires_at="2026-10-01T00:00:00Z",
        )

    assert response["id"] == "key-1"
    req.assert_called_once()
    assert req.call_args.args == (
        "POST",
        "/api/v1/service-accounts/credentials/api-keys/key-1/reissue",
    )
    assert req.call_args.kwargs["json_body"] == {"expires_at": "2026-10-01T00:00:00Z"}

def test_cloudru_service_account_management_uses_documented_resource():
    client = CloudRuIamClient(key_id="id", key_secret="secret")
    with patch.object(client, "_request", return_value={"service_accounts": [{"id": "sa-1", "name": "Alice Pro"}]}) as req:
        accounts = client.list_service_accounts()
    assert accounts == [{"id": "sa-1", "name": "Alice Pro"}]
    req.assert_called_once_with("GET", "/api/v1/service-accounts")

    with patch.object(client, "_request", return_value={"id": "sa-2"}) as req:
        response = client.create_service_account(
            project_id="project-1",
            name="Alice Pro",
            description="Foundation Models runtime",
        )
    assert response["id"] == "sa-2"
    req.assert_called_once_with(
        "POST",
        "/api/v1/service-accounts",
        json_body={
            "name": "Alice Pro",
            "description": "Foundation Models runtime",
            "target": {"project_id": "project-1"},
        },
    )
