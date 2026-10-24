import json
import pickle
from unittest.mock import Mock

import pytest

from cloud.base import CloudProviderError
from secret_store.cloudru import CloudRuSecretResolver
from secret_store.core import SecretErrorCode, SecretRef, SecretResolutionError, SecretValue
from secret_store.fake import FakeSecretResolver


def _ref(version="v1", provider="cloudru"):
    return SecretRef(provider=provider, secret_id="secret-1", version_id=version, purpose="test")


def test_secret_ref_contains_metadata_only():
    ref = _ref()
    assert ref.metadata() == {
        "provider": "cloudru",
        "secret_id": "secret-1",
        "version_id": "v1",
        "purpose": "test",
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"provider": "", "secret_id": "x", "version_id": "v1"},
        {"provider": "cloudru", "secret_id": "", "version_id": "v1"},
        {"provider": "cloudru", "secret_id": "x", "version_id": ""},
    ],
)
def test_secret_ref_rejects_missing_identity(kwargs):
    with pytest.raises(ValueError, match="required"):
        SecretRef(**kwargs)


def test_secret_value_has_no_plaintext_string_json_or_pickle_surface():
    value = SecretValue("canary-secret")
    assert value.reveal() == "canary-secret"
    assert str(value) == "[REDACTED]"
    assert repr(value) == "<SecretValue redacted>"
    with pytest.raises(TypeError):
        json.dumps(value)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(value)


def test_secret_value_rejects_empty_text():
    with pytest.raises(ValueError, match="non-empty"):
        SecretValue("")


def test_fake_resolver_success_missing_inactive_unavailable_and_version_switch():
    v1 = _ref("v1")
    v2 = _ref("v2")
    fake = FakeSecretResolver({v1: "old-value"})
    assert fake.resolve(v1).reveal() == "old-value"

    with pytest.raises(SecretResolutionError) as missing:
        fake.resolve(v2)
    assert missing.value.code == SecretErrorCode.NOT_FOUND

    fake.put(v2, "new-value", active=False)
    with pytest.raises(SecretResolutionError) as inactive:
        fake.resolve(v2)
    assert inactive.value.code == SecretErrorCode.VERSION_INACTIVE

    fake.put(v2, "new-value", active=True)
    assert fake.resolve(v2).reveal() == "new-value"

    fake.available = False
    with pytest.raises(SecretResolutionError) as unavailable:
        fake.resolve(v2)
    assert unavailable.value.code == SecretErrorCode.UNAVAILABLE


def test_cloudru_resolver_wraps_active_version_value():
    client = Mock()
    client.get_version_status.return_value = "ACTIVE"
    client.get_secret_value.return_value = "canary-secret"
    resolver = CloudRuSecretResolver(client)

    value = resolver.resolve(_ref())

    assert value.reveal() == "canary-secret"
    client.get_version_status.assert_called_once_with("secret-1", "v1")
    client.get_secret_value.assert_called_once_with("secret-1", "v1")


def test_cloudru_resolver_rejects_other_provider_without_backend_call():
    client = Mock()
    resolver = CloudRuSecretResolver(client)

    with pytest.raises(SecretResolutionError) as error:
        resolver.resolve(_ref(provider="android"))

    assert error.value.code == SecretErrorCode.INVALID_REFERENCE
    client.get_version_status.assert_not_called()


@pytest.mark.parametrize("status", ["disabled", "revoked", "unknown"])
def test_cloudru_resolver_fails_closed_for_non_active_status(status):
    client = Mock()
    client.get_version_status.return_value = status
    resolver = CloudRuSecretResolver(client)

    with pytest.raises(SecretResolutionError) as error:
        resolver.resolve(_ref())

    assert error.value.code == SecretErrorCode.VERSION_INACTIVE
    client.get_secret_value.assert_not_called()


@pytest.mark.parametrize(
    ("provider_code", "expected"),
    [
        ("not_found", SecretErrorCode.NOT_FOUND),
        ("version_disabled", SecretErrorCode.VERSION_INACTIVE),
        ("auth_failed", SecretErrorCode.AUTH_FAILED),
        ("auth_not_configured", SecretErrorCode.AUTH_FAILED),
        ("authorization_failed", SecretErrorCode.AUTH_FAILED),
        ("provider_unavailable", SecretErrorCode.UNAVAILABLE),
        ("invalid_response", SecretErrorCode.PROVIDER_ERROR),
    ],
)
def test_cloudru_resolver_maps_provider_errors(provider_code, expected):
    client = Mock()
    client.get_version_status.side_effect = CloudProviderError("safe", code=provider_code)
    resolver = CloudRuSecretResolver(client)

    with pytest.raises(SecretResolutionError) as error:
        resolver.resolve(_ref())

    assert error.value.code == expected
    assert "safe" not in str(error.value)


def test_secret_resolution_error_message_never_contains_reference_identity():
    ref = _ref()
    error = SecretResolutionError(SecretErrorCode.NOT_FOUND, ref)
    message = str(error)
    assert "secret-1" not in message
    assert "v1" not in message
