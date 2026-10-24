"""Provider keys are encrypted when an encryption key is configured, and stored as given otherwise."""

import base64

from secret_store.credential_crypto import decrypt_secret, encrypt_secret


def test_plaintext_passthrough_without_the_encryption_key(monkeypatch):
    monkeypatch.delenv("LOGOS_PROVIDER_CREDENTIAL_KEY", raising=False)
    assert encrypt_secret("provider-key-value") == "provider-key-value"
    assert decrypt_secret("provider-key-value") == "provider-key-value"


def test_encrypted_round_trip_with_a_configured_key(monkeypatch):
    monkeypatch.setenv(
        "LOGOS_PROVIDER_CREDENTIAL_KEY", base64.urlsafe_b64encode(b"2" * 32).decode("ascii")
    )
    token = encrypt_secret("provider-key-value")
    assert token != "provider-key-value"
    assert decrypt_secret(token) == "provider-key-value"
