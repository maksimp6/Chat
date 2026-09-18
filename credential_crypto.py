"""Encrypted storage boundary for deployment-wide provider credentials."""
from __future__ import annotations

import os


class CredentialCryptoError(RuntimeError):
    """Raised when provider credential encryption is not configured."""


def _fernet():
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError as exc:  # pragma: no cover
        raise CredentialCryptoError(
            "cryptography is required for encrypted provider credentials"
        ) from exc

    key = os.getenv("ALICE_PROVIDER_CREDENTIAL_KEY")
    if not key:
        raise CredentialCryptoError(
            "Set ALICE_PROVIDER_CREDENTIAL_KEY to a Fernet key"
        )
    try:
        return Fernet(key.encode("utf-8")), InvalidToken
    except Exception as exc:
        raise CredentialCryptoError("Invalid ALICE_PROVIDER_CREDENTIAL_KEY") from exc


def encrypt_secret(value: str) -> str:
    fernet, _ = _fernet()
    return fernet.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    fernet, InvalidToken = _fernet()
    try:
        return fernet.decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialCryptoError("Unable to decrypt provider credential") from exc
