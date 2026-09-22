from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

from cloudru_api_key_provider import CloudRuApiKeyProvider


def test_validate_key_uses_foundation_models_api_key_auth():
    response = Mock()
    response.raise_for_status.return_value = None

    with patch("cloudru_api_key_provider.requests.get", return_value=response) as get:
        CloudRuApiKeyProvider(base_url="https://foundation-models.example/v1").validate_key(
            "cloudru-secret"
        )

    kwargs = get.call_args.kwargs
    assert get.call_args.args[0] == "https://foundation-models.example/v1/models"
    assert kwargs["headers"]["Authorization"] == "Api-Key cloudru-secret"


def test_validate_key_maps_401_to_unauthorized():
    response = Mock(status_code=401)
    error = Exception("unauthorized")
    response.raise_for_status.side_effect = error
    error.response = response

    with patch("cloudru_api_key_provider.requests.get", return_value=response):
        with pytest.raises(PermissionError):
            CloudRuApiKeyProvider().validate_key("bad")


def test_rotation_requires_provider_id_and_management_credentials(monkeypatch):
    provider = CloudRuApiKeyProvider()
    monkeypatch.delenv("CLOUDRU_IAM_KEY_ID", raising=False)
    monkeypatch.delenv("CLOUDRU_IAM_KEY_SECRET", raising=False)
    assert provider.rotation_supported("key-1") is False


def test_reissue_keeps_same_provider_key_id():
    iam = Mock()
    iam.reissue_api_key.return_value = {
        "id": "cloudru-key-1",
        "secret": "new-secret",
    }
    provider = CloudRuApiKeyProvider(iam_client=iam)

    with patch.object(provider, "rotation_supported", return_value=True):
        key_id, secret = provider.reissue_key(
            "cloudru-key-1",
            expires_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )

    assert (key_id, secret) == ("cloudru-key-1", "new-secret")
    iam.reissue_api_key.assert_called_once()
    assert iam.reissue_api_key.call_args.kwargs["api_key_id"] == "cloudru-key-1"
    assert iam.reissue_api_key.call_args.kwargs["expires_at"] == "2026-10-01T00:00:00Z"
