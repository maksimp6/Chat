"""Cloud.ru implementation of the provider-neutral secret resolver."""

from __future__ import annotations

from secret_store.core import (
    SecretErrorCode,
    SecretRef,
    SecretResolutionError,
    SecretValue,
)
from typing import Any
from secret_store.host import bound


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
    def __init__(self, client: Any) -> None:
        self._client = client

    def resolve(self, ref: SecretRef) -> SecretValue:
        if ref.provider != "cloudru":
            raise SecretResolutionError(SecretErrorCode.INVALID_REFERENCE, ref)

        try:
            status = (
                self._client.get_version_status(ref.secret_id, ref.version_id)
                .strip()
                .lower()
            )
            if status not in _ACTIVE_STATUSES:
                raise SecretResolutionError(SecretErrorCode.VERSION_INACTIVE, ref)
            value = self._client.get_secret_value(ref.secret_id, ref.version_id)
        except SecretResolutionError:
            raise
        except bound.CloudProviderError as exc:
            code = _ERROR_MAP.get(exc.code, SecretErrorCode.PROVIDER_ERROR)
            raise SecretResolutionError(code, ref) from exc

        return SecretValue(value)


__all__ = ["CloudRuSecretResolver"]
