#!/usr/bin/env python3
"""Rotate the deployment-wide Cloud.ru Foundation Models API key.

Cloud.ru reissues the existing API-key resource, changing its secret and
expiration while retaining the provider key ID. The replacement is validated
against Foundation Models before the local credential is promoted.
"""

from __future__ import annotations
from core.env import env_get

import logging
from datetime import timedelta

from cloud.cloudru.api_key_provider import CloudRuApiKeyProvider
from cloud.cloudru.iam import CloudRuIamClient
from secret_store.credential_crypto import decrypt_secret, encrypt_secret
from storage.db import get_store
from provider_accounts.credentials import (
    CLOUDRU,
    list_provider_credentials,
    create_schema,
    rotation_needed,
    get_cloudru_iam_credentials,
)
from provider_accounts.key_rotation import rotate_active_key
from observability.trace_manager import traced_operation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("logos_cloudru_provider_key_rotation")


def _ttl() -> timedelta:
    try:
        days = int(env_get("CLOUDRU_KEY_TTL_DAYS", "1"))
    except ValueError as exc:
        raise RuntimeError("CLOUDRU_KEY_TTL_DAYS must be an integer") from exc
    if not 1 <= days <= 365:
        raise RuntimeError("CLOUDRU_KEY_TTL_DAYS must be between 1 and 365")
    return timedelta(days=days)


@traced_operation("cloudru.rotation")
def main() -> int:
    conn = get_store()
    try:
        create_schema(conn)
        row = next(
            (
                item
                for item in list_provider_credentials(conn, provider=CLOUDRU)
                if item["status"] == "active"
            ),
            None,
        )
        if not row:
            logger.error("No active Cloud.ru provider key to rotate")
            return 2

        if not rotation_needed(row["expires_at"]):
            logger.info("Cloud.ru provider key is not inside the rotation window")
            return 0

        management = get_cloudru_iam_credentials(conn, decrypt_secret)
        if not management:
            logger.error("Cloud.ru rotation unavailable: IAM credentials are not configured")
            return 2
        if management.get("expires_at"):
            from datetime import datetime, timezone

            master_expires = datetime.fromisoformat(management["expires_at"].replace("Z", "+00:00"))
            # A timestamp without an offset is read as UTC; comparing it with an aware time would raise.
            if master_expires.tzinfo is None:
                master_expires = master_expires.replace(tzinfo=timezone.utc)
            if master_expires <= datetime.now(timezone.utc):
                logger.error("Cloud.ru rotation blocked: IAM master key is expired")
                return 2
        provider = CloudRuApiKeyProvider(
            iam_client=CloudRuIamClient(
                key_id=management["key_id"],
                key_secret=management["key_secret"],
            )
        )
        if not provider.rotation_supported(row["provider_key_id"]):
            logger.error(
                "Cloud.ru rotation unavailable: provider key ID or IAM management credentials are missing"
            )
            return 2

        row = next(
            (
                item
                for item in list_provider_credentials(conn, provider=CLOUDRU)
                if item["status"] == "active"
            ),
            None,
        )
        if not row or not rotation_needed(row["expires_at"]):
            return 0

        new_id, issued_at, expires_at = rotate_active_key(
            db=conn,
            provider=provider,
            encrypt=encrypt_secret,
            old_id=int(row["id"]),
            # A reissue keeps the key's project; an empty id would overwrite the saved one.
            project_id=str(row["project_id"] or ""),
            old_provider_key_id=row["provider_key_id"],
            provider_name=CLOUDRU,
            reissue_existing=True,
            ttl=_ttl(),
        )
        logger.info(
            "Rotated Cloud.ru Foundation Models provider key id=%s expires_at=%s",
            new_id,
            expires_at.isoformat(),
        )
        return 0
    except Exception:
        logger.exception("Cloud.ru provider-key rotation failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
