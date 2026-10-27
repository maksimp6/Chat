"""Cloud.ru implementation of the provider-neutral secret resolver."""

from __future__ import annotations

from core.wikifunctions import lowercase_string, trim_string

from cloud.base import CloudProviderError
from cloud.cloudru.secret_management import CloudRuSecretManagementClient
from secret_store.core import (
    SecretErrorCode,
    SecretRef,
    SecretResolutionError,
    SecretValue,
)


_ERROR_MAP = {
    "not_found": SecretErrorCode.NOT_FOUND,
    "version_disabled": SecretErrorCode.VERSION_INACTIVE,
    "auth_failed": SecretErrorCode.AUTH_FAILED,
    "auth_not_configured": SecretErrorCode.AUTH_FAILED,
    "authorization_failed": SecretErrorCode.AUTH_FAILED,
    "provider_unavailable": SecretErrorCode.UNAVAILABLE,
}

_ACTIVE_STATUSES = {"active", "enabled"}


class CloudRuSecretResolver:
    def __init__(self, client: CloudRuSecretManagementClient) -> None:
        self._client = client

    def resolve(self, ref: SecretRef) -> SecretValue:
        if ref.provider != "cloudru":
            raise SecretResolutionError(SecretErrorCode.INVALID_REFERENCE, ref)

        try:
            status = lowercase_string(
                trim_string(self._client.get_version_status(ref.secret_id, ref.version_id))
            )
            if status not in _ACTIVE_STATUSES:
                raise SecretResolutionError(SecretErrorCode.VERSION_INACTIVE, ref)
            value = self._client.get_secret_value(ref.secret_id, ref.version_id)
        except SecretResolutionError:
            raise
        except CloudProviderError as exc:
            code = _ERROR_MAP.get(exc.code, SecretErrorCode.PROVIDER_ERROR)
            raise SecretResolutionError(code, ref) from exc

        return SecretValue(value)


__all__ = ["CloudRuSecretResolver"]
