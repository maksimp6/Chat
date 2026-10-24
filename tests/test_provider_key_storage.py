"""Provider keys are never stored in plaintext."""

import base64

import pytest

from secret_store import credential_crypto
from secret_store.credential_crypto import CredentialCryptoError, decrypt_secret, encrypt_secret


def test_encrypt_refuses_to_store_without_the_encryption_key(monkeypatch):
    monkeypatch.delenv("LOGOS_PROVIDER_CREDENTIAL_KEY", raising=False)
    with pytest.raises(CredentialCryptoError):
        encrypt_secret("provider-key-value")


def test_encrypted_round_trip_with_a_configured_key(monkeypatch):
    monkeypatch.setenv(
        "LOGOS_PROVIDER_CREDENTIAL_KEY", base64.urlsafe_b64encode(b"2" * 32).decode("ascii")
    )
    token = encrypt_secret("provider-key-value")
    assert token != "provider-key-value"
    assert decrypt_secret(token) == "provider-key-value"
