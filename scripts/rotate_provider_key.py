#!/usr/bin/env python3
"""Rotate the single deployment-wide Yandex API key.

Run this worker at least hourly. It rotates when the active key has one hour or
less remaining. The generated replacement is valid for exactly 12 hours.
"""
from __future__ import annotations

import logging

from credential_crypto import encrypt_secret
from db import get_conn
from provider_credentials import _fetch_one, create_schema, rotation_needed
from provider_key_rotation import rotate_active_key
from yandex_api_key_provider import YandexApiKeyProvider

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("alice_provider_key_rotation")


def main() -> int:
    conn = get_conn()
    try:
        create_schema(conn)
        row = _fetch_one(
            conn,
            """SELECT id, yandex_key_id, project_id, expires_at
               FROM provider_credentials
               WHERE status = 'active'
               LIMIT 1""",
        )
        if not row:
            logger.error("No active global provider key to rotate")
            return 2
        if not rotation_needed(row["expires_at"]):
            logger.info("Global provider key is not inside the one-hour rotation window")
            return 0

        conn.execute("BEGIN IMMEDIATE")
        # Re-read after acquiring the write lock so a second worker cannot use
        # stale rotation metadata.
        row = _fetch_one(
            conn,
            """SELECT id, yandex_key_id, project_id, expires_at
               FROM provider_credentials
               WHERE status = 'active'
               LIMIT 1""",
        )
        if not row or not rotation_needed(row["expires_at"]):
            conn.rollback()
            return 0

        provider = YandexApiKeyProvider(project_id=str(row["project_id"]))
        new_id, issued_at, expires_at = rotate_active_key(
            db=conn,
            provider=provider,
            encrypt=encrypt_secret,
            old_id=int(row["id"]),
            project_id=str(row["project_id"]),
            old_provider_key_id=row["yandex_key_id"],
            commit_before_revoke=True,
        )
        conn.commit()
        logger.info(
            "Rotated global Yandex provider key id=%s expires_at=%s",
            new_id,
            expires_at.isoformat(),
        )
        return 0
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.exception("Global provider-key rotation failed")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
