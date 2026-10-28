"""Import runner-issued Yandex keys; this module never contacts a provider."""

import hmac
import re
from datetime import UTC, datetime, timedelta
from typing import cast

from provider_accounts.credentials import (
    bootstrap_credential,
    get_active_credential,
    record_health_check,
    replace_active_credential,
)
from provider_accounts.host import bound, fingerprint_key, get_store


class ImportRejected(ValueError):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


def encryption_ready():
    # Works with both deployed and renamed configurations, without disclosing the key.
    probe = "logos-issued-key-encryption-probe"
    try:
        encrypted = bound.encrypt_secret(probe)
        return encrypted != probe and bound.decrypt_secret(encrypted) == probe
    except Exception:
        return False


def _active_row(db):
    identity = db.get("provider/active/yandex")
    row = (
        db.get("provider/credential/" + str(identity)) if identity is not None else None
    )
    return identity, row


def _credential_summary(row):
    return (
        {
            k: row.get(k)
            for k in ("fingerprint", "provider_key_id", "project_id", "expires_at")
        }
        if row
        else None
    )


def issuance_status():
    _, row = _active_row(get_store())
    return {
        "encryption_ready": encryption_ready(),
        "configured": row is not None,
        "credential": _credential_summary(row),
    }


def _date(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(UTC)
    except (AttributeError, TypeError, ValueError):
        raise ImportRejected("invalid_key_lifetime") from None


def import_yandex_key(payload):
    if not isinstance(payload, dict):
        raise ImportRejected("json_object_required")
    if not encryption_ready():
        raise ImportRejected("encryption_not_configured", 503)
    for name in ("project_id", "provider_key_id"):
        if not isinstance(payload.get(name), str) or not re.fullmatch(
            r"[a-z0-9]{20}", payload[name]
        ):
            raise ImportRejected("invalid_" + name)
    secret = payload.get("api_key")
    if (
        not isinstance(secret, str)
        or not 1 <= len(secret) <= 4096
        or not secret.isascii()
        or any(c.isspace() for c in secret)
    ):
        raise ImportRejected("invalid_api_key")
    issued, expires = _date(payload.get("issued_at")), _date(payload.get("expires_at"))
    now = datetime.now(UTC)
    # Runner IAM keys live 12 hours; console API keys can live up to 90 days.
    if (
        issued > now + timedelta(minutes=5)
        or expires <= now
        or not timedelta(0) < expires - issued <= timedelta(days=90)
    ):
        raise ImportRejected("invalid_key_lifetime")
    db = get_store()
    # One read yields the active id and its row, so the replacement below is checked against the same key.
    active_id, active_row = _active_row(db)
    current = _credential_summary(active_row)
    fingerprint = fingerprint_key(secret)
    # An expired key is replaced by the runner's new key; a key still valid is never overwritten.
    replacing = bool(current) and _date(current["expires_at"]) <= now
    if (
        current
        and not replacing
        and (
            current["fingerprint"] != fingerprint
            or current["provider_key_id"] != payload["provider_key_id"]
            or current["project_id"] != payload["project_id"]
            or _date(current["expires_at"]) != expires
        )
    ):
        raise ImportRejected("provider_already_configured", 409)
    # The native transaction also rejects a competing initial writer.
    try:
        if replacing:
            replace_active_credential(
                db,
                secret,
                payload["project_id"],
                bound.encrypt_secret,
                "yandex",
                provider_key_id=payload["provider_key_id"],
                now=issued,
                ttl=expires - issued,
                expected_active_id=active_id,
            )
        else:
            bootstrap_credential(
                db,
                secret,
                payload["project_id"],
                bound.encrypt_secret,
                now=issued,
                decrypt=bound.decrypt_secret,
                provider="yandex",
                provider_key_id=payload["provider_key_id"],
                ttl=expires - issued,
            )
    except Exception:
        raise ImportRejected("credential_storage_failed", 503) from None
    stored = get_active_credential(db, bound.decrypt_secret, provider="yandex")
    if not hmac.compare_digest(stored.api_key, secret):
        raise ImportRejected("credential_readback_failed", 503)
    record_health_check(db, "yandex", status="configured", error=None)
    return {
        "provider": "yandex",
        "status": "configured",
        "storage_verified": True,
        "authorization_ok": False,
        "fingerprint": stored.fingerprint,
        "provider_key_id": stored.provider_key_id,
        # get_active_credential always fills expires_at from the stored row.
        "expires_at": cast(datetime, stored.expires_at).isoformat(),
    }
