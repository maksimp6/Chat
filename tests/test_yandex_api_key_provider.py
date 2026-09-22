from datetime import datetime, timezone
from unittest.mock import Mock, patch

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


@patch("yandex_api_key_provider.requests.post")
def test_validate_key_uses_provider_authentication(mock_post):
    response = Mock()
    mock_post.return_value = response

    provider().validate_key("new-provider-secret")

    response.raise_for_status.assert_called_once()
    kwargs = mock_post.call_args.kwargs
    assert kwargs["headers"]["Authorization"] == "Api-Key new-provider-secret"
    assert kwargs["json"]["model"] == "gpt://project-1/alice-lite/latest"
    assert "new-provider-secret" in kwargs["headers"]["Authorization"]


@patch("yandex_api_key_provider.requests.post")
def test_validate_key_reports_yandex_http_401_without_secret_in_error(mock_post):
    response = Mock()
    response.status_code = 401
    response.text = '{"message":"Permission denied"}'
    error = __import__("requests").HTTPError("401", response=response)
    response.raise_for_status.side_effect = error
    mock_post.return_value = response

    import pytest
    with pytest.raises(PermissionError, match="HTTP 401"):
        provider().validate_key("secret-must-not-appear")

    assert "secret-must-not-appear" not in str(response.text)
