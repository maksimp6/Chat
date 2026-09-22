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

    key_id, secret = provider().create_key(
        expires_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    )

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


@patch("yandex_api_key_provider.requests.get")
def test_validate_key_lists_models_without_inference(mock_get):
    response = Mock()
    mock_get.return_value = response

    provider().validate_key("new-provider-secret")

    response.raise_for_status.assert_called_once()
    assert mock_get.call_args.args[0].endswith("/models")
    kwargs = mock_get.call_args.kwargs
    assert kwargs["headers"]["Authorization"] == "Api-Key new-provider-secret"
    assert kwargs["headers"]["x-project"] == "project-1"
    assert "json" not in kwargs
    assert not hasattr(provider(), "validation_model")



@patch("yandex_api_key_provider.requests.get")
def test_validate_key_maps_models_permission_failure(mock_get):
    from requests import HTTPError
    from yandex_api_key_provider import YandexProviderPermissionError

    response = Mock()
    response.status_code = 403
    response.text = '{"detail":"permission denied"}'
    response.raise_for_status.side_effect = HTTPError("403", response=response)
    mock_get.return_value = response

    with pytest.raises(YandexProviderPermissionError, match="not authorized to list AI Studio models"):
        provider().validate_key("provider-secret")

    kwargs = mock_get.call_args.kwargs
    assert kwargs["headers"]["Authorization"] == "Api-Key provider-secret"
    assert kwargs["headers"]["x-project"] == "project-1"


@patch("yandex_api_key_provider.requests.post")
def test_create_key_defaults_to_model_view_scope(mock_post, monkeypatch):
    monkeypatch.delenv("YANDEX_API_KEY_SCOPES", raising=False)
    response = Mock()
    response.json.return_value = {
        "apiKey": {"id": "aje-key-2"},
        "secret": "secret-value-2",
    }
    mock_post.return_value = response

    YandexApiKeyProvider(iam_token="iam-secret", service_account_id="sa-1", project_id="project-1").create_key(expires_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc))

    assert "yc.ai.models.viewer" in mock_post.call_args.kwargs["json"]["scopes"]
