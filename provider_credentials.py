"""Global Yandex provider-key lifecycle primitives.

The active credential is global, not user-scoped. Plaintext keys are kept at
runtime only and must never be logged, persisted, or returned by API routes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

KEY_TTL = timedelta(hours=12)
ROTATE_BEFORE = timedelta(hours=1)
ACTIVE = "active"
ROTATING = "rotating"
REVOKED = "revoked"
DISABLED = "disabled"


class CredentialError(RuntimeError):
    """Base error for provider credential failures."""


class NoActiveCredentialError(CredentialError):
    pass


class ExpiredCredentialError(CredentialError):
    pass


@dataclass(frozen=True)
class ProviderCredential:
    api_key: str
    project_id: str


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def create_schema(db: Any) -> None:
    """Create the global credential table and supporting indexes."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS provider_credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key_encrypted TEXT NOT NULL,
            yandex_key_id TEXT,
            issued_at TIMESTAMP NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_key
        ON provider_credentials (status) WHERE status = 'active'
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_provider_credentials_status_expires
        ON provider_credentials (status, expires_at)
    """)


def get_active_key(db: Any, decrypt: Callable[[str], str], now: Optional[datetime] = None) -> str:
    """Resolve the one active, non-expired global key."""
    row = db.fetch_one("""
        SELECT api_key_encrypted, expires_at
        FROM provider_credentials
        WHERE status = 'active'
        LIMIT 1
    """)
    if not row:
        raise NoActiveCredentialError("No active provider credential")
    if _as_utc(now or utcnow()) >= _as_utc(row["expires_at"]):
        raise ExpiredCredentialError("Active provider credential expired")
    return decrypt(row["api_key_encrypted"])


def rotation_needed(expires_at: datetime | str, now: Optional[datetime] = None) -> bool:
    return _as_utc(expires_at) - _as_utc(now or utcnow()) <= ROTATE_BEFORE


def should_revoke(expires_at: datetime | str, now: Optional[datetime] = None) -> bool:
    return _as_utc(now or utcnow()) >= _as_utc(expires_at)


def promote_rotated_key(db: Any, old_id: int, encrypted_key: str, yandex_key_id: str,
                        issued_at: datetime, expires_at: datetime) -> None:
    """Replace the active key inside the caller's transaction."""
    db.execute(
        "UPDATE provider_credentials SET status = 'rotating' WHERE id = ? AND status = 'active'",
        (old_id,),
    )
    db.execute("""
        INSERT INTO provider_credentials
        (api_key_encrypted, yandex_key_id, issued_at, expires_at, status)
        VALUES (?, ?, ?, ?, 'active')
    """, (encrypted_key, yandex_key_id, issued_at, expires_at))


def issue_window(now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """Return the issuance and expiry timestamps for a fresh 12-hour key."""
    issued = _as_utc(now or utcnow())
    return issued, issued + KEY_TTL


def resolve_client_api_key(config: Any, db: Any = None,
                           decrypt: Optional[Callable[[str], str]] = None) -> str:
    """Resolve the global key, with legacy env fallback for bootstrap deployments."""
    if db is not None and decrypt is not None:
        return get_active_key(db, decrypt)
    key = getattr(config, "API_KEY", None)
    if not key:
        raise NoActiveCredentialError("No global provider key configured")
    return key
