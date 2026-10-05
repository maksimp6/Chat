"""Provider-neutral Secret Store resolver contract and backends.

Public surface:

- ``SecretReference`` — immutable non-secret pointer (purpose + secret_id + version_id).
- ``SecretResolverError`` — typed exception, safe to log, no plaintext.
- ``SecretBackend`` — protocol that backends must satisfy.
- ``SecretResolver`` — thin resolver that delegates to any backend.
- ``CloudRuSecretBackend`` — production backend wrapping the shipped Cloud.ru adapter.
- ``FakeSecretBackend`` — deterministic in-memory backend for unit tests.
- Error code constants: ``NOT_FOUND``, ``VERSION_DISABLED``, ``PROVIDER_UNAVAILABLE``,
  ``AUTH_FAILED``, ``AUTH_NOT_CONFIGURED``, ``INVALID_RESPONSE``.
"""

from secret_store.cloudru_backend import CloudRuSecretBackend
from secret_store.contract import (
    AUTH_FAILED,
    AUTH_NOT_CONFIGURED,
    INVALID_RESPONSE,
    NOT_FOUND,
    PROVIDER_UNAVAILABLE,
    VERSION_DISABLED,
    SecretBackend,
    SecretReference,
    SecretResolverError,
)
from secret_store.fake_backend import FakeSecretBackend
from secret_store.resolver import SecretResolver

__all__ = [
    "AUTH_FAILED",
    "AUTH_NOT_CONFIGURED",
    "INVALID_RESPONSE",
    "NOT_FOUND",
    "PROVIDER_UNAVAILABLE",
    "VERSION_DISABLED",
    "CloudRuSecretBackend",
    "FakeSecretBackend",
    "SecretBackend",
    "SecretReference",
    "SecretResolver",
    "SecretResolverError",
]
