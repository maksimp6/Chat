"""Encrypted storage boundary for deployment-wide provider credentials."""
from __future__ import annotations

import base64
import hashlib
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

    key = os.getenv("ALICE_PROVIDER_CREDENTIAL_KEY", "").strip()
    if not key:
        return None, InvalidToken

    # Accept any non-empty deployment secret. Derive a stable Fernet key
    # instead of requiring callers to provide Fernet's exact key format.
    derived = base64.urlsafe_b64encode(
        hashlib.sha256(key.encode("utf-8")).digest()
    )
    return Fernet(derived), InvalidToken


def encrypt_secret(value: str) -> str:
    fernet, _ = _fernet()
    if fernet is None:
        return value
    return fernet.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    fernet, InvalidToken = _fernet()
    if fernet is None:
        return value
    # Older deployments could persist credentials before the encryption key
    # was configured. Keep those records readable so a deployment can migrate
    # them to encrypted storage without forcing the operator to re-enter the key.
    if not value.startswith("gAAAA"):
        return value
    try:
        return fernet.decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialCryptoError("Unable to decrypt provider credential") from exc
