"""Deployment-wide provider key rotation.

scripts/rotate_provider_key.py (Yandex) and scripts/rotate_cloudru_provider_key.py
(Cloud.ru Foundation Models) run these hourly. Yandex: the active key is replaced
when it has one hour or less left, by a key valid for exactly 12 hours. Cloud.ru
reissues the existing API-key resource (new secret and expiry, same provider key
ID) and validates it against Foundation Models before promoting it.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from provider_accounts.credentials import (
    CLOUDRU,
    create_schema,
    get_cloudru_iam_credentials,
    list_provider_credentials,
    rotation_needed,
)
from provider_accounts.key_rotation import rotate_active_key
from provider_accounts.host import bound, get_store, traced_operation

logger = logging.getLogger("logos.provider_key_rotation")
cloudru_logger = logging.getLogger("logos.cloudru_provider_key_rotation")


def rotate_yandex_key() -> int:
    """Rotate the Yandex key when it has one hour or less left; 0 ok, 1 failed, 2 not possible."""
    conn = get_store()
    try:
        create_schema(conn)
        row = next(
            (
                item
                for item in list_provider_credentials(conn, provider="yandex")
                if item["status"] == "active"
            ),
            None,
        )
        if not row:
            logger.error("No active global provider key to rotate")
            return 2
        if not rotation_needed(row["expires_at"]):
            logger.info(
                "Global provider key is not inside the one-hour rotation window"
            )
            return 0

        # Re-read the active key before rotating. Nothing here takes a lock: a concurrent rotation is
        # caught only by the active-key check inside rotate_active_key.
        row = next(
            (
                item
                for item in list_provider_credentials(conn, provider="yandex")
                if item["status"] == "active"
            ),
            None,
        )
        if not row or not rotation_needed(row["expires_at"]):
            return 0

        provider = bound.YandexApiKeyProvider(project_id=str(row["project_id"]))
        new_id, issued_at, expires_at = rotate_active_key(
            db=conn,
            provider=provider,
            encrypt=bound.encrypt_secret,
            old_id=int(row["id"]),
            project_id=str(row["project_id"]),
            old_provider_key_id=row["provider_key_id"] or row["yandex_key_id"],
            commit_before_revoke=True,
            provider_name="yandex",
        )
        logger.info(
            "Rotated global Yandex provider key id=%s expires_at=%s",
            new_id,
            expires_at.isoformat(),
        )
        return 0
    except Exception:
        logger.exception("Global provider-key rotation failed")
        return 1


def cloudru_key_ttl() -> timedelta:
    try:
        days = int(os.environ.get("CLOUDRU_KEY_TTL_DAYS", "1"))
    except ValueError as exc:
        raise RuntimeError("CLOUDRU_KEY_TTL_DAYS must be an integer") from exc
    if not 1 <= days <= 365:
        raise RuntimeError("CLOUDRU_KEY_TTL_DAYS must be between 1 and 365")
    return timedelta(days=days)


@traced_operation("cloudru.rotation")
def rotate_cloudru_key() -> int:
    """Reissue the Cloud.ru key inside its rotation window; 0 ok, 1 failed, 2 not possible."""
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
            cloudru_logger.error("No active Cloud.ru provider key to rotate")
            return 2

        if not rotation_needed(row["expires_at"]):
            cloudru_logger.info(
                "Cloud.ru provider key is not inside the rotation window"
            )
            return 0

        management = get_cloudru_iam_credentials(conn, bound.decrypt_secret)
        if not management:
            cloudru_logger.error(
                "Cloud.ru rotation unavailable: IAM credentials are not configured"
            )
            return 2
        if management.get("expires_at"):
            from datetime import datetime, timezone

            master_expires = datetime.fromisoformat(
                management["expires_at"].replace("Z", "+00:00")
            )
            # A timestamp without an offset is read as UTC; comparing it with an aware time would raise.
            if master_expires.tzinfo is None:
                master_expires = master_expires.replace(tzinfo=timezone.utc)
            if master_expires <= datetime.now(timezone.utc):
                cloudru_logger.error(
                    "Cloud.ru rotation blocked: IAM master key is expired"
                )
                return 2
        provider = bound.CloudRuApiKeyProvider(
            iam_client=bound.CloudRuIamClient(
                key_id=management["key_id"],
                key_secret=management["key_secret"],
            )
        )
        if not provider.rotation_supported(row["provider_key_id"]):
            cloudru_logger.error(
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
            encrypt=bound.encrypt_secret,
            old_id=int(row["id"]),
            # A reissue keeps the key's project; an empty id would overwrite the saved one.
            project_id=str(row["project_id"] or ""),
            old_provider_key_id=row["provider_key_id"],
            commit_before_revoke=True,
            provider_name=CLOUDRU,
            reissue_existing=True,
            ttl=cloudru_key_ttl(),
        )
        cloudru_logger.info(
            "Rotated Cloud.ru Foundation Models provider key id=%s expires_at=%s",
            new_id,
            expires_at.isoformat(),
        )
        return 0
    except Exception:
        cloudru_logger.exception("Cloud.ru provider-key rotation failed")
        return 1
