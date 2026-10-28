#!/usr/bin/env python3
"""Rotate the single deployment-wide Yandex API key.

Run this worker at least hourly. It rotates when the active key has one hour or
less remaining. The generated replacement is valid for exactly 12 hours.
"""

from __future__ import annotations

import logging

from secret_store.credential_crypto import encrypt_secret
from storage.db import get_store
from provider_accounts.credentials import (
    list_provider_credentials,
    create_schema,
    rotation_needed,
)
from provider_accounts.key_rotation import rotate_active_key
from yandex_client_modules.api_key_provider import YandexApiKeyProvider

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("logos_provider_key_rotation")


def main() -> int:
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

        provider = YandexApiKeyProvider(project_id=str(row["project_id"]))
        new_id, issued_at, expires_at = rotate_active_key(
            db=conn,
            provider=provider,
            encrypt=encrypt_secret,
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


if __name__ == "__main__":
    from composition import wiring

    wiring.register_all()
    raise SystemExit(main())
