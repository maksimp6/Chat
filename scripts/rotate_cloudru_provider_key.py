#!/usr/bin/env python3
"""Rotate the deployment-wide Cloud.ru Foundation Models API key.

Cloud.ru reissues the existing API-key resource, changing its secret and
expiration while retaining the provider key ID. The replacement is validated
against Foundation Models before the local credential is promoted.
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta

from cloudru_api_key_provider import CloudRuApiKeyProvider
from credential_crypto import encrypt_secret
from db import get_conn
from provider_credentials import (
    CLOUDRU,
    _fetch_one,
    create_schema,
    rotation_needed,
)
from provider_key_rotation import rotate_active_key

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("alice_cloudru_provider_key_rotation")


def _ttl() -> timedelta:
    try:
        days = int(os.getenv("CLOUDRU_KEY_TTL_DAYS", "1"))
    except ValueError as exc:
        raise RuntimeError("CLOUDRU_KEY_TTL_DAYS must be an integer") from exc
    if not 1 <= days <= 365:
        raise RuntimeError("CLOUDRU_KEY_TTL_DAYS must be between 1 and 365")
    return timedelta(days=days)


def main() -> int:
    conn = get_conn()
    try:
        create_schema(conn)
        row = _fetch_one(
            conn,
            """SELECT id, provider_key_id, project_id, expires_at
               FROM provider_credentials
               WHERE provider = ? AND status = 'active'
               LIMIT 1""",
            (CLOUDRU,),
        )
        if not row:
            logger.error("No active Cloud.ru provider key to rotate")
            return 2

        if not rotation_needed(row["expires_at"]):
            logger.info("Cloud.ru provider key is not inside the rotation window")
            return 0

        provider = CloudRuApiKeyProvider()
        if not provider.rotation_supported(row["provider_key_id"]):
            logger.error(
                "Cloud.ru rotation unavailable: provider key ID or IAM management credentials are missing"
            )
            return 2

        conn.execute("BEGIN IMMEDIATE")
        row = _fetch_one(
            conn,
            """SELECT id, provider_key_id, project_id, expires_at
               FROM provider_credentials
               WHERE provider = ? AND status = 'active'
               LIMIT 1""",
            (CLOUDRU,),
        )
        if not row or not rotation_needed(row["expires_at"]):
            conn.rollback()
            return 0

        new_id, issued_at, expires_at = rotate_active_key(
            db=conn,
            provider=provider,
            encrypt=encrypt_secret,
            old_id=int(row["id"]),
            project_id="",
            old_provider_key_id=row["provider_key_id"],
            commit_before_revoke=True,
            provider_name=CLOUDRU,
            reissue_existing=True,
            ttl=_ttl(),
        )
        conn.commit()
        logger.info(
            "Rotated Cloud.ru Foundation Models provider key id=%s expires_at=%s",
            new_id,
            expires_at.isoformat(),
        )
        return 0
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.exception("Cloud.ru provider-key rotation failed")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
