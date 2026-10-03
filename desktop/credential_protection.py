"""System-keyring provisioning for the desktop credential encryption key."""

from __future__ import annotations

import os
import secrets


SERVICE_NAME = "Alice Pro"
KEY_NAME = "provider-credential-key"


class DesktopCredentialProtectionError(RuntimeError):
    """Raised when the desktop credential key cannot be stored safely."""


def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise DesktopCredentialProtectionError(
            "A system keyring backend is required for desktop credentials"
        ) from exc
    return keyring


def ensure_provider_credential_key(_data_dir=None) -> str:
    configured = os.environ.get("ALICE_PROVIDER_CREDENTIAL_KEY", "").strip()
    if configured:
        return configured

    keyring = _keyring()
    try:
        existing = keyring.get_password(SERVICE_NAME, KEY_NAME)
    except Exception as exc:
        raise DesktopCredentialProtectionError(
            "Unable to read the desktop credential key from the system keyring"
        ) from exc

    if existing:
        os.environ["ALICE_PROVIDER_CREDENTIAL_KEY"] = existing
        return existing

    generated = secrets.token_urlsafe(48)
    try:
        keyring.set_password(SERVICE_NAME, KEY_NAME, generated)
        stored = keyring.get_password(SERVICE_NAME, KEY_NAME)
    except Exception as exc:
        raise DesktopCredentialProtectionError(
            "Unable to store the desktop credential key in the system keyring"
        ) from exc

    if stored != generated:
        raise DesktopCredentialProtectionError(
            "System keyring did not persist the desktop credential key"
        )

    os.environ["ALICE_PROVIDER_CREDENTIAL_KEY"] = generated
    return generated
