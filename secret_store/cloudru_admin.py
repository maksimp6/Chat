"""Cloud.ru implementation of SecretAdminBackend."""

from __future__ import annotations

import re
import uuid

from cloud.cloudru.secret_management_admin import CloudRuSecretManagementAdminClient
from secret_store.admin import SecretAdminResult
from secret_store.core import SecretRef, SecretValue

_SAFE = re.compile(r"[^a-z0-9-]+")


class CloudRuSecretAdminBackend:
    def __init__(self, client: CloudRuSecretManagementAdminClient) -> None:
        self._client = client

    def create(self, *, purpose: str, value: SecretValue) -> SecretAdminResult:
        stem = _SAFE.sub("-", purpose.lower()).strip("-") or "secret"
        name = f"alice-{stem[:32]}-{uuid.uuid4().hex[:12]}"
        secret_id, version_id = self._client.create_secret(
            name=name,
            value=value.reveal(),
            description="Managed by Alice Pro",
        )
        return SecretAdminResult(
            ref=SecretRef(
                provider="cloudru",
                secret_id=secret_id,
                version_id=version_id,
                purpose=purpose,
            ),
            created=True,
        )

    def rotate(self, ref: SecretRef, value: SecretValue) -> SecretAdminResult:
        if ref.provider != "cloudru":
            raise ValueError("Cloud.ru admin backend requires cloudru SecretRef")
        version_id = self._client.create_version(
            secret_id=ref.secret_id,
            value=value.reveal(),
        )
        return SecretAdminResult(
            ref=SecretRef(
                provider=ref.provider,
                secret_id=ref.secret_id,
                version_id=version_id,
                purpose=ref.purpose,
            ),
            created=False,
        )

    def delete(self, ref: SecretRef) -> None:
        if ref.provider != "cloudru":
            raise ValueError("Cloud.ru admin backend requires cloudru SecretRef")
        self._client.delete_secret(ref.secret_id)


__all__ = ["CloudRuSecretAdminBackend"]
