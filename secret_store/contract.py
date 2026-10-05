"""Provider-neutral Secret Store contract types.

No plaintext secret value appears in any serializable field of these types.
All types are safe to persist, log, and pass through the #776 InvocationContext
boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Stable error codes shared across all backend implementations.
# Mirrors the Cloud.ru-specific codes so the Cloud.ru adapter needs no
# translation for the common paths.
NOT_FOUND = "not_found"
VERSION_DISABLED = "version_disabled"
PROVIDER_UNAVAILABLE = "provider_unavailable"
AUTH_FAILED = "auth_failed"
AUTH_NOT_CONFIGURED = "auth_not_configured"
INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True)
class SecretReference:
    """Immutable pointer to one specific version of a named secret.

    Contains only non-secret identifiers. Safe to persist, log, and
    serialize. Never holds a plaintext value.
    """

    purpose: str
    secret_id: str
    version_id: str

    def __post_init__(self) -> None:
        if not self.purpose or not self.secret_id or not self.version_id:
            raise ValueError("purpose, secret_id and version_id are required")


class SecretResolverError(Exception):
    """Raised when a secret cannot be resolved.

    Contains only typed metadata (error code, sanitized message, reference).
    Safe to log, trace, and serialize — never contains plaintext.
    """

    def __init__(
        self,
        code: str,
        message: str,
        reference: SecretReference | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.reference = reference
        super().__init__(str(self))

    def __str__(self) -> str:
        ref_info = f" (purpose={self.reference.purpose})" if self.reference else ""
        return f"{self.code}: {self.message}{ref_info}"


@runtime_checkable
class SecretBackend(Protocol):
    """Provider-neutral interface for resolving a pinned secret reference.

    Implementations must:
    - Return the plaintext value only to the direct caller; never cache it.
    - Register the resolved value with the current trace via
      ``trace.register_sensitive_value`` before returning.
    - Raise ``SecretResolverError`` (never return ``None`` or silently fail).
    - Never include plaintext in exception messages, trace events, or logs.
    """

    def resolve(self, reference: SecretReference) -> str:  # pragma: no cover
        """Return the plaintext value for *reference*.

        Raises ``SecretResolverError`` if the secret is missing, revoked,
        unavailable, or unauthenticated.
        """
        ...


__all__ = [
    "AUTH_FAILED",
    "AUTH_NOT_CONFIGURED",
    "INVALID_RESPONSE",
    "NOT_FOUND",
    "PROVIDER_UNAVAILABLE",
    "VERSION_DISABLED",
    "SecretBackend",
    "SecretReference",
    "SecretResolverError",
]
