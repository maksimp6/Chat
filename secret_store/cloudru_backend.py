"""Cloud.ru Secret Management adapter implementing SecretBackend.

Wraps the already-shipped ``CloudRuSecretManagementClient`` without changing
its pinned-version semantics, trace-registration behavior, or security
constraints. All proven security properties of the underlying client are
preserved by delegation rather than reimplementation.
"""

from __future__ import annotations

from cloud.base import CloudProviderError
from cloud.cloudru.secret_management import CloudRuSecretManagementClient
from secret_store.contract import (
    AUTH_FAILED,
    AUTH_NOT_CONFIGURED,
    INVALID_RESPONSE,
    NOT_FOUND,
    PROVIDER_UNAVAILABLE,
    VERSION_DISABLED,
    SecretReference,
    SecretResolverError,
)

# Map Cloud.ru-specific error codes to the canonical contract codes.
# Keys are the ``code=`` values raised by CloudRuSecretManagementClient.
_CLOUDRU_CODE_MAP: dict[str, str] = {
    "auth_failed": AUTH_FAILED,
    "auth_not_configured": AUTH_NOT_CONFIGURED,
    "authorization_failed": AUTH_FAILED,
    "not_found": NOT_FOUND,
    "version_disabled": VERSION_DISABLED,
    "provider_unavailable": PROVIDER_UNAVAILABLE,
    "invalid_response": INVALID_RESPONSE,
    "provider_http_error": PROVIDER_UNAVAILABLE,
}


class CloudRuSecretBackend:
    """SecretBackend adapter backed by Cloud.ru Secret Management.

    Uses the existing viewer-scoped IAM identity
    (``CLOUDRU_SECRET_MANAGEMENT_KEY_ID``/``CLOUDRU_SECRET_MANAGEMENT_KEY_SECRET``)
    and pinned-version resolution already implemented in
    ``CloudRuSecretManagementClient``. Translates ``CloudProviderError`` to
    ``SecretResolverError`` so callers need not depend on the Cloud.ru-specific
    exception hierarchy.
    """

    def __init__(self, client: CloudRuSecretManagementClient | None = None) -> None:
        self._client = client or CloudRuSecretManagementClient()

    def resolve(self, reference: SecretReference) -> str:
        """Delegate to the Cloud.ru client; translate errors to the contract type."""
        try:
            return self._client.get_secret_value(reference.secret_id, reference.version_id)
        except CloudProviderError as exc:
            normalized = _CLOUDRU_CODE_MAP.get(exc.code, PROVIDER_UNAVAILABLE)
            raise SecretResolverError(
                code=normalized,
                message=exc.message,
                reference=reference,
            ) from exc


__all__ = ["CloudRuSecretBackend"]
