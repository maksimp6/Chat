from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

from yandex_api_key_provider import YandexApiKeyProvider


def provider():
    return YandexApiKeyProvider(
        iam_token="iam-secret",
        service_account_id="sa-1",
        scopes=["yc.ai.languageModels.execute"],
        project_id="project-1",
    )


@patch("yandex_api_key_provider.requests.post")
def test_create_key_returns_resource_id_and_secret(mock_post):
    response = Mock()
    response.json.return_value = {
        "apiKey": {"id": "aje-key-1"},
        "secret": "secret-value",
    }
    mock_post.return_value = response

    key_id, secret = provider().create_key(expires_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc))

    response.raise_for_status.assert_called_once()
    assert (key_id, secret) == ("aje-key-1", "secret-value")
    body = mock_post.call_args.kwargs["json"]
    assert body["expiresAt"] == "2026-01-01T12:00:00Z"
    assert "iam-secret" not in str(body)


@patch("yandex_api_key_provider.requests.delete")
def test_revoke_key_calls_yandex_resource_endpoint(mock_delete):
    response = Mock()
    mock_delete.return_value = response

    provider().revoke_key("aje-key-old")

    response.raise_for_status.assert_called_once()
    assert mock_delete.call_args.args[0].endswith("/iam/v1/apiKeys/aje-key-old")
    assert mock_delete.call_args.kwargs["headers"]["Authorization"] == "Bearer iam-secret"


def test_validate_key_is_local_only():
    with patch("yandex_api_key_provider.requests.get") as mock_get:
        provider().validate_key("provider-secret")
        mock_get.assert_not_called()


@patch("yandex_api_key_provider.requests.post")
def test_create_key_defaults_to_runtime_ai_scopes(mock_post, monkeypatch):
    monkeypatch.delenv("YANDEX_API_KEY_SCOPES", raising=False)
    response = Mock()
    response.json.return_value = {
        "apiKey": {"id": "aje-key-2"},
        "secret": "secret-value-2",
    }
    mock_post.return_value = response

    YandexApiKeyProvider(
        iam_token="iam-secret",
        service_account_id="sa-1",
        project_id="project-1",
    ).create_key(expires_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc))

    scopes = mock_post.call_args.kwargs["json"]["scopes"]
    assert "yc.ai.languageModels.execute" in scopes
    assert "yc.ai.models.viewer" not in scopes
