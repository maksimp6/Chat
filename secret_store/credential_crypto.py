"""Encrypted storage boundary for deployment-wide provider credentials."""

from __future__ import annotations
from core.env import env_get

import base64
import hashlib


class CredentialCryptoError(RuntimeError):
    """Raised when provider credential encryption is not configured."""


def _fernet():
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError as exc:  # pragma: no cover
        raise CredentialCryptoError(
            "cryptography is required for encrypted provider credentials"
        ) from exc

    key = env_get("LOGOS_PROVIDER_CREDENTIAL_KEY", "").strip()
    if not key:
        return None, InvalidToken

    # Accept any non-empty deployment secret. Derive a stable Fernet key
    # instead of requiring callers to provide Fernet's exact key format.
    derived = base64.urlsafe_b64encode(hashlib.sha256(key.encode("utf-8")).digest())
    return Fernet(derived), InvalidToken


def encryption_configured() -> bool:
    return bool(env_get("LOGOS_PROVIDER_CREDENTIAL_KEY", "").strip())


def encrypt_secret(value: str) -> str:
    fernet, _ = _fernet()
    if fernet is None:
        return value
    return fernet.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    fernet, InvalidToken = _fernet()
    # Plaintext records (stored while no key was configured) stay readable as given.
    if not value.startswith("gAAAA"):
        return value
    if fernet is None:
        # Returning ciphertext here would send the token upstream as if it were the key.
        raise CredentialCryptoError(
            "Encrypted provider credential cannot be read without its encryption key"
        )
    try:
        return fernet.decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialCryptoError("Unable to decrypt provider credential") from exc
