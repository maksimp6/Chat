"""Global Yandex provider-key lifecycle and resolution primitives.

The deployment uses one provider API key at a time. Plaintext is kept only at
the backend boundary. Traces receive a stable non-secret identifier, never the
secret itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
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
    id: Optional[int] = None
    yandex_key_id: Optional[str] = None
    issued_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    fingerprint: Optional[str] = None

    @property
    def trace_key_id(self) -> str:
        if self.yandex_key_id:
            return str(self.yandex_key_id)
        if self.fingerprint:
            return f"sha256:{self.fingerprint}"
        return "environment"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def fingerprint_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _fetch_one(db: Any, query: str, params: tuple = ()):
    if hasattr(db, "fetch_one"):
        return db.fetch_one(query)
    return db.execute(query, params).fetchone()


def create_schema(db: Any) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS provider_credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key_encrypted TEXT NOT NULL,
            yandex_key_id TEXT,
            project_id TEXT NOT NULL,
            issued_at TIMESTAMP NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    columns = {
        row["name"]
        for row in db.execute("PRAGMA table_info(provider_credentials)").fetchall()
    }
    if "project_id" not in columns:
        db.execute(
            "ALTER TABLE provider_credentials ADD COLUMN project_id TEXT NOT NULL DEFAULT ''"
        )

    db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_key
        ON provider_credentials (status) WHERE status = 'active'
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_provider_credentials_status_expires
        ON provider_credentials (status, expires_at)
    """)


def get_active_credential(
    db: Any,
    decrypt: Callable[[str], str],
    now: Optional[datetime] = None,
) -> ProviderCredential:
    row = _fetch_one(db, """
        SELECT id, api_key_encrypted, yandex_key_id, project_id, issued_at, expires_at
        FROM provider_credentials
        WHERE status = 'active'
        LIMIT 1
    """)
    if not row:
        raise NoActiveCredentialError("No active provider credential")

    expires_at = _as_utc(row["expires_at"])
    current = _as_utc(now or utcnow())
    if current >= expires_at:
        raise ExpiredCredentialError("Active provider credential expired")

    secret = decrypt(row["api_key_encrypted"])
    return ProviderCredential(
        api_key=secret,
        project_id=str(row["project_id"]),
        id=int(row["id"]),
        yandex_key_id=row["yandex_key_id"],
        issued_at=_as_utc(row["issued_at"]),
        expires_at=expires_at,
        fingerprint=fingerprint_key(secret),
    )


def get_active_key(db: Any, decrypt: Callable[[str], str], now: Optional[datetime] = None) -> str:
    return get_active_credential(db, decrypt, now).api_key


def bootstrap_credential(
    db: Any,
    api_key: str,
    project_id: str,
    encrypt: Callable[[str], str],
    now: Optional[datetime] = None,
    decrypt: Optional[Callable[[str], str]] = None,
) -> ProviderCredential:
    """Seed the global store from the deployment secret once."""
    create_schema(db)
    existing = _fetch_one(
        db,
        "SELECT id FROM provider_credentials WHERE status = 'active' LIMIT 1",
    )
    if existing:
        if decrypt is None:
            raise NoActiveCredentialError("Another process already bootstrapped the provider key")
        return get_active_credential(db, decrypt, now)

    issued_at, expires_at = issue_window(now)
    encrypted = encrypt(api_key)
    key_id = f"bootstrap-{fingerprint_key(api_key)[:16]}"
    cursor = db.execute("""
        INSERT INTO provider_credentials
        (api_key_encrypted, yandex_key_id, project_id, issued_at, expires_at, status)
        VALUES (?, ?, ?, ?, ?, 'active')
    """, (encrypted, key_id, project_id, issued_at, expires_at))
    if hasattr(db, "commit"):
        db.commit()

    return ProviderCredential(
        api_key=api_key,
        project_id=project_id,
        id=getattr(cursor, "lastrowid", None),
        yandex_key_id=key_id,
        issued_at=issued_at,
        expires_at=expires_at,
        fingerprint=fingerprint_key(api_key),
    )


def resolve_client_credential(
    config: Any,
    db: Any = None,
    decrypt: Optional[Callable[[str], str]] = None,
    encrypt: Optional[Callable[[str], str]] = None,
) -> ProviderCredential:
    """Resolve the deployment-wide key, bootstrapping from env when necessary."""
    if db is not None:
        if decrypt is None:
            existing = _fetch_one(
                db,
                "SELECT id FROM provider_credentials WHERE status = 'active' LIMIT 1",
            )
            if existing:
                raise CredentialError(
                    "Active provider credential exists but encryption is not configured"
                )
        else:
            try:
                return get_active_credential(db, decrypt)
            except NoActiveCredentialError:
                bootstrap_key = getattr(config, "API_KEY", None)
                if bootstrap_key and encrypt is not None:
                    return bootstrap_credential(
                        db,
                        bootstrap_key,
                        str(getattr(config, "PROJECT_ID", "")),
                        encrypt,
                        decrypt=decrypt,
                    )
                if bootstrap_key:
                    return ProviderCredential(
                        api_key=bootstrap_key,
                        project_id=str(getattr(config, "PROJECT_ID", "")),
                        fingerprint=fingerprint_key(bootstrap_key),
                    )
                raise

        key = getattr(config, "API_KEY", None)
        if not key:
            raise NoActiveCredentialError("No global provider key configured")
        return ProviderCredential(
            api_key=key,
            project_id=str(getattr(config, "PROJECT_ID", "")),
            fingerprint=fingerprint_key(key),
        )

    key = getattr(config, "API_KEY", None)
    if not key:
        raise NoActiveCredentialError("No global provider key configured")
    return ProviderCredential(
        api_key=key,
        project_id=str(getattr(config, "PROJECT_ID", "")),
        fingerprint=fingerprint_key(key),
    )


def rotation_needed(expires_at: datetime | str, now: Optional[datetime] = None) -> bool:
    return _as_utc(expires_at) - _as_utc(now or utcnow()) <= ROTATE_BEFORE


def should_revoke(expires_at: datetime | str, now: Optional[datetime] = None) -> bool:
    return _as_utc(now or utcnow()) >= _as_utc(expires_at)


def promote_rotated_key(
    db: Any,
    old_id: int,
    encrypted_key: str,
    yandex_key_id: str,
    project_id: str,
    issued_at: datetime,
    expires_at: datetime,
) -> None:
    db.execute(
        "UPDATE provider_credentials SET status = 'rotating' "
        "WHERE id = ? AND status = 'active'",
        (old_id,),
    )
    db.execute("""
        INSERT INTO provider_credentials
        (api_key_encrypted, yandex_key_id, project_id, issued_at, expires_at, status)
        VALUES (?, ?, ?, ?, ?, 'active')
    """, (encrypted_key, yandex_key_id, project_id, issued_at, expires_at))


def issue_window(now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    issued = _as_utc(now or utcnow())
    return issued, issued + KEY_TTL
