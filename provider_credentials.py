"""Provider-aware deployment credential storage and resolution.

Each provider owns an independent active credential. Plaintext exists only at
backend boundaries. Persistent records contain encrypted secrets and
non-secret provider metadata/fingerprints.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import threading
from typing import Any, Callable, Optional

from trace_manager import get_current_trace

KEY_TTL = timedelta(hours=12)
ROTATE_BEFORE = timedelta(hours=1)
ACTIVE = "active"
ROTATING = "rotating"
REVOKED = "revoked"
DISABLED = "disabled"

YANDEX = "yandex"
CLOUDRU = "cloudru"
SUPPORTED_PROVIDERS = (YANDEX, CLOUDRU)


class CredentialError(RuntimeError):
    """Base error for provider credential failures."""


class NoActiveCredentialError(CredentialError):
    pass


class ExpiredCredentialError(CredentialError):
    pass


@dataclass(frozen=True)
class ProviderCredential:
    api_key: str
    project_id: str = ""
    provider: str = YANDEX
    id: Optional[int] = None
    provider_key_id: Optional[str] = None
    yandex_key_id: Optional[str] = None
    issued_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    fingerprint: Optional[str] = None

    @property
    def trace_key_id(self) -> str:
        if self.provider_key_id:
            return str(self.provider_key_id)
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
        CREATE TABLE IF NOT EXISTS cloudru_iam_credentials (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            key_id TEXT NOT NULL,
            key_secret_encrypted TEXT NOT NULL,
            project_id TEXT NOT NULL DEFAULT '',
            service_account_id TEXT,
            expires_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS provider_credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key_encrypted TEXT NOT NULL,
            yandex_key_id TEXT,
            provider_key_id TEXT,
            provider TEXT NOT NULL DEFAULT 'yandex',
            project_id TEXT NOT NULL DEFAULT '',
            issued_at TIMESTAMP NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            fingerprint TEXT,
            last_checked_at TIMESTAMP,
            last_check_status TEXT,
            last_check_error TEXT
        )
    """)

    iam_columns = {
        row["name"] for row in db.execute("PRAGMA table_info(cloudru_iam_credentials)").fetchall()
    }
    if "expires_at" not in iam_columns:
        db.execute("ALTER TABLE cloudru_iam_credentials ADD COLUMN expires_at TIMESTAMP")

    columns = {
        row["name"] for row in db.execute("PRAGMA table_info(provider_credentials)").fetchall()
    }

    migrations = (
        (
            "project_id",
            "ALTER TABLE provider_credentials ADD COLUMN project_id TEXT NOT NULL DEFAULT ''",
        ),
        (
            "provider",
            "ALTER TABLE provider_credentials ADD COLUMN provider TEXT NOT NULL DEFAULT 'yandex'",
        ),
        ("provider_key_id", "ALTER TABLE provider_credentials ADD COLUMN provider_key_id TEXT"),
        ("yandex_key_id", "ALTER TABLE provider_credentials ADD COLUMN yandex_key_id TEXT"),
        ("fingerprint", "ALTER TABLE provider_credentials ADD COLUMN fingerprint TEXT"),
        (
            "last_checked_at",
            "ALTER TABLE provider_credentials ADD COLUMN last_checked_at TIMESTAMP",
        ),
        ("last_check_status", "ALTER TABLE provider_credentials ADD COLUMN last_check_status TEXT"),
        ("last_check_error", "ALTER TABLE provider_credentials ADD COLUMN last_check_error TEXT"),
    )
    for column, statement in migrations:
        if column not in columns:
            db.execute(statement)

    # Existing installations contain Yandex rows, so preserve their old ID.
    db.execute("""
        UPDATE provider_credentials
        SET provider_key_id = yandex_key_id
        WHERE provider = 'yandex'
          AND (provider_key_id IS NULL OR provider_key_id = '')
          AND yandex_key_id IS NOT NULL
    """)
    # Never treat locally generated placeholder IDs as remotely managed key IDs.
    db.execute("""
        UPDATE provider_credentials
        SET provider_key_id = NULL
        WHERE provider_key_id LIKE 'bootstrap-%'
           OR provider_key_id LIKE 'managed-%'
    """)

    # Old schema allowed only one active key globally. Drop that constraint
    # before creating the provider-scoped equivalent.
    db.execute("DROP INDEX IF EXISTS one_active_key")
    db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_key_per_provider
        ON provider_credentials (provider) WHERE status = 'active'
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_provider_credentials_provider_status_expires
        ON provider_credentials (provider, status, expires_at)
    """)

    # Cloud.ru Secret Management refs: only the (immutable) secret/version
    # identifiers live here, never a secret value. See SecretManagementRef.
    db.execute("""
        CREATE TABLE IF NOT EXISTS secret_management_refs (
            purpose TEXT PRIMARY KEY,
            secret_id TEXT NOT NULL,
            pinned_version_id TEXT NOT NULL,
            previous_version_id TEXT,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _parse_expiry(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str) and value.strip():
        try:
            return _as_utc(value.strip())
        except ValueError:
            return None
    return None


def get_cloudru_iam_credentials(db: Any, decrypt: Callable[[str], str]) -> Optional[dict[str, str]]:
    create_schema(db)
    row = db.execute(
        "SELECT key_id, key_secret_encrypted, project_id, service_account_id, expires_at "
        "FROM cloudru_iam_credentials WHERE id = 1"
    ).fetchone()
    if not row:
        return None
    expires_at = _parse_expiry(row["expires_at"])
    return {
        "key_id": str(row["key_id"]),
        "key_secret": decrypt(row["key_secret_encrypted"]),
        "project_id": str(row["project_id"] or ""),
        "service_account_id": str(row["service_account_id"] or ""),
        "expires_at": expires_at.isoformat() if expires_at else "",
    }


def save_cloudru_iam_credentials(
    db: Any,
    *,
    key_id: str,
    key_secret: str,
    project_id: str,
    service_account_id: Optional[str],
    expires_at: Optional[datetime],
    encrypt: Callable[[str], str],
) -> None:
    if not key_id.strip() or not key_secret:
        raise ValueError("Cloud.ru IAM key_id and key_secret are required")
    if expires_at is not None:
        expires_at = _as_utc(expires_at)
        if expires_at <= utcnow():
            raise ValueError("Cloud.ru IAM master key is expired")
    create_schema(db)
    db.execute(
        """
        INSERT INTO cloudru_iam_credentials
        (id, key_id, key_secret_encrypted, project_id, service_account_id, expires_at)
        VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            key_id = excluded.key_id,
            key_secret_encrypted = excluded.key_secret_encrypted,
            project_id = excluded.project_id,
            service_account_id = excluded.service_account_id,
            expires_at = excluded.expires_at
    """,
        (key_id.strip(), encrypt(key_secret), project_id.strip(), service_account_id, expires_at),
    )
    db.commit()


def get_cloudru_iam_credentials(db: Any, decrypt: Callable[[str], str]) -> Optional[dict[str, str]]:
    create_schema(db)
    row = db.execute(
        "SELECT key_id, key_secret_encrypted, project_id, service_account_id "
        "FROM cloudru_iam_credentials WHERE id = 1"
    ).fetchone()
    if not row:
        return None
    return {
        "key_id": str(row["key_id"]),
        "key_secret": decrypt(row["key_secret_encrypted"]),
        "project_id": str(row["project_id"] or ""),
        "service_account_id": str(row["service_account_id"] or ""),
    }


def save_cloudru_iam_credentials(
    db: Any,
    *,
    key_id: str,
    key_secret: str,
    project_id: str,
    service_account_id: Optional[str],
    encrypt: Callable[[str], str],
) -> None:
    if not key_id.strip() or not key_secret:
        raise ValueError("Cloud.ru IAM key_id and key_secret are required")
    create_schema(db)
    db.execute(
        """
        INSERT INTO cloudru_iam_credentials
        (id, key_id, key_secret_encrypted, project_id, service_account_id)
        VALUES (1, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            key_id = excluded.key_id,
            key_secret_encrypted = excluded.key_secret_encrypted,
            project_id = excluded.project_id,
            service_account_id = excluded.service_account_id
    """,
        (key_id.strip(), encrypt(key_secret), project_id.strip(), service_account_id),
    )
    db.commit()


def get_active_credential(
    db: Any,
    decrypt: Callable[[str], str],
    now: Optional[datetime] = None,
    provider: str = YANDEX,
) -> ProviderCredential:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")

    row = _fetch_one(
        db,
        """
        SELECT id, api_key_encrypted, provider_key_id, yandex_key_id,
               provider, project_id, issued_at, expires_at
        FROM provider_credentials
        WHERE provider = ? AND status = 'active'
        LIMIT 1
    """,
        (provider,),
    )
    if not row:
        raise NoActiveCredentialError(f"No active {provider} provider credential")

    expires_at = _as_utc(row["expires_at"])
    current = _as_utc(now or utcnow())
    if current >= expires_at:
        raise ExpiredCredentialError(f"Active {provider} provider credential expired")

    secret = decrypt(row["api_key_encrypted"])
    row_provider = (
        row["provider"]
        if (hasattr(row, "keys") and "provider" in row.keys())
        else (row.get("provider") if isinstance(row, dict) else None)
    ) or provider
    row_provider_key_id = (
        row["provider_key_id"]
        if (hasattr(row, "keys") and "provider_key_id" in row.keys())
        else (row.get("provider_key_id") if isinstance(row, dict) else None)
    )
    return ProviderCredential(
        api_key=secret,
        project_id=str(row["project_id"] or ""),
        provider=str(row_provider),
        id=int(row["id"]),
        provider_key_id=row_provider_key_id or row["yandex_key_id"],
        yandex_key_id=row["yandex_key_id"],
        issued_at=_as_utc(row["issued_at"]),
        expires_at=expires_at,
        fingerprint=fingerprint_key(secret),
    )


def get_active_key(
    db: Any,
    decrypt: Callable[[str], str],
    now: Optional[datetime] = None,
    provider: str = YANDEX,
) -> str:
    return get_active_credential(db, decrypt, now, provider=provider).api_key


def bootstrap_credential(
    db: Any,
    api_key: str,
    project_id: str,
    encrypt: Callable[[str], str],
    now: Optional[datetime] = None,
    decrypt: Optional[Callable[[str], str]] = None,
    provider: str = YANDEX,
    provider_key_id: Optional[str] = None,
    ttl: timedelta = KEY_TTL,
) -> ProviderCredential:
    """Seed one provider's active credential from an environment/UI secret."""
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    if not api_key:
        raise ValueError("api_key must be non-empty")

    create_schema(db)
    existing = _fetch_one(
        db,
        "SELECT id FROM provider_credentials WHERE provider = ? AND status = 'active' LIMIT 1",
        (provider,),
    )
    if existing:
        if decrypt is None:
            raise NoActiveCredentialError(
                f"Another process already bootstrapped the {provider} provider key"
            )
        return get_active_credential(db, decrypt, now, provider=provider)

    issued_at, expires_at = issue_window(now, ttl=ttl)
    encrypted = encrypt(api_key)
    key_id = provider_key_id
    cursor = db.execute(
        """
        INSERT INTO provider_credentials
        (api_key_encrypted, yandex_key_id, provider_key_id, provider,
         project_id, issued_at, expires_at, status, fingerprint)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
    """,
        (
            encrypted,
            key_id if provider == YANDEX else None,
            key_id,
            provider,
            project_id,
            issued_at,
            expires_at,
            fingerprint_key(api_key),
        ),
    )
    if hasattr(db, "commit"):
        db.commit()

    return ProviderCredential(
        api_key=api_key,
        project_id=project_id,
        provider=provider,
        id=getattr(cursor, "lastrowid", None),
        provider_key_id=key_id,
        yandex_key_id=key_id if provider == YANDEX else None,
        issued_at=issued_at,
        expires_at=expires_at,
        fingerprint=fingerprint_key(api_key),
    )


def replace_active_credential(
    db: Any,
    api_key: str,
    project_id: str,
    encrypt: Callable[[str], str],
    provider: str,
    *,
    provider_key_id: Optional[str] = None,
    now: Optional[datetime] = None,
    ttl: timedelta = KEY_TTL,
) -> ProviderCredential:
    """Atomically replace the provider's active credential in local storage."""
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    if not api_key:
        raise ValueError("api_key must be non-empty")

    create_schema(db)
    issued_at, expires_at = issue_window(now, ttl=ttl)
    encrypted = encrypt(api_key)

    db.execute(
        "UPDATE provider_credentials SET status = 'rotating' "
        "WHERE provider = ? AND status = 'active'",
        (provider,),
    )
    key_id = provider_key_id
    cursor = db.execute(
        """
        INSERT INTO provider_credentials
        (api_key_encrypted, yandex_key_id, provider_key_id, provider,
         project_id, issued_at, expires_at, status, fingerprint)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
    """,
        (
            encrypted,
            key_id if provider == YANDEX else None,
            key_id,
            provider,
            project_id,
            issued_at,
            expires_at,
            fingerprint_key(api_key),
        ),
    )
    if hasattr(db, "commit"):
        db.commit()

    return ProviderCredential(
        api_key=api_key,
        project_id=project_id,
        provider=provider,
        id=getattr(cursor, "lastrowid", None),
        provider_key_id=key_id,
        yandex_key_id=key_id if provider == YANDEX else None,
        issued_at=issued_at,
        expires_at=expires_at,
        fingerprint=fingerprint_key(api_key),
    )


def resolve_client_credential(
    config: Any,
    db: Any = None,
    decrypt: Optional[Callable[[str], str]] = None,
    encrypt: Optional[Callable[[str], str]] = None,
    provider: str = YANDEX,
) -> ProviderCredential:
    """Resolve a provider credential exclusively from persistent storage."""
    if db is None:
        raise NoActiveCredentialError(
            f"No database configured for {provider} provider credential resolution"
        )
    if decrypt is None:
        existing = _fetch_one(
            db,
            "SELECT id FROM provider_credentials WHERE provider = ? AND status = 'active' LIMIT 1",
            (provider,),
        )
        if existing:
            raise CredentialError(
                f"Active {provider} provider credential exists but encryption is not configured"
            )
        raise NoActiveCredentialError(f"No global {provider} provider key configured")
    return get_active_credential(db, decrypt, provider=provider)


def list_provider_credentials(
    db: Any,
    provider: Optional[str] = None,
) -> list[dict[str, Any]]:
    create_schema(db)
    if provider:
        rows = db.execute(
            """
            SELECT id, provider, provider_key_id, yandex_key_id,
                   project_id, issued_at, expires_at, status, created_at,
                   fingerprint, last_checked_at, last_check_status, last_check_error
            FROM provider_credentials
            WHERE provider = ?
            ORDER BY id DESC
        """,
            (provider,),
        ).fetchall()
    else:
        rows = db.execute("""
            SELECT id, provider, provider_key_id, yandex_key_id,
                   project_id, issued_at, expires_at, status, created_at
            FROM provider_credentials
            ORDER BY id DESC
        """).fetchall()
    return [dict(row) for row in rows]


def rotation_needed(expires_at: datetime | str, now: Optional[datetime] = None) -> bool:
    return _as_utc(expires_at) - _as_utc(now or utcnow()) <= ROTATE_BEFORE


def should_revoke(expires_at: datetime | str, now: Optional[datetime] = None) -> bool:
    return _as_utc(now or utcnow()) >= _as_utc(expires_at)


def promote_rotated_key(
    db: Any,
    old_id: int,
    encrypted_key: str,
    provider_key_id: str,
    project_id: str,
    issued_at: datetime,
    expires_at: datetime,
    provider: str = YANDEX,
    fingerprint: Optional[str] = None,
) -> None:
    db.execute(
        "UPDATE provider_credentials SET status = 'rotating' "
        "WHERE id = ? AND provider = ? AND status = 'active'",
        (old_id, provider),
    )
    db.execute(
        """
        INSERT INTO provider_credentials
        (api_key_encrypted, yandex_key_id, provider_key_id, provider,
         project_id, issued_at, expires_at, status, fingerprint)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
    """,
        (
            encrypted_key,
            provider_key_id if provider == YANDEX else None,
            provider_key_id,
            provider,
            project_id,
            issued_at,
            expires_at,
            fingerprint,
        ),
    )


def issue_window(
    now: Optional[datetime] = None,
    ttl: timedelta = KEY_TTL,
) -> tuple[datetime, datetime]:
    issued = _as_utc(now or utcnow())
    if ttl <= timedelta(0):
        raise ValueError("ttl must be positive")
    return issued, issued + ttl


def record_health_check(
    db: Any,
    provider: str,
    *,
    status: str,
    error: Optional[str] = None,
    checked_at: Optional[datetime] = None,
    commit: bool = True,
) -> None:
    """Persist only the result of a provider health check, never its secret."""
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    if status not in {"configured", "connected", "invalid", "unavailable", "unknown"}:
        raise ValueError(f"Unsupported health status: {status}")
    db.execute(
        """UPDATE provider_credentials
           SET last_checked_at = ?, last_check_status = ?, last_check_error = ?
           WHERE provider = ? AND status = 'active'""",
        (
            _as_utc(checked_at or utcnow()),
            status,
            error,
            provider,
        ),
    )
    if commit and hasattr(db, "commit"):
        db.commit()


@dataclass(frozen=True)
class SecretManagementRef:
    """A local pointer to one immutable Cloud.ru Secret Management version.

    Cloud.ru versions cannot be edited in place, so "rotate" and "rollback"
    are purely local: which version_id this app currently trusts for a given
    purpose (e.g. ``alice_short_token``, ``github_oauth_client_secret``,
    ``provider_credential:yandex``, ``alice_database_url``). The value itself
    is never stored here; it is resolved on demand through
    ``resolve_secret_management_value``.
    """

    purpose: str
    secret_id: str
    pinned_version_id: str
    previous_version_id: Optional[str]
    updated_at: datetime


def set_secret_management_ref(
    db: Any,
    purpose: str,
    secret_id: str,
    version_id: str,
    *,
    now: Optional[datetime] = None,
) -> SecretManagementRef:
    """Pin (or switch) one purpose's Cloud.ru Secret Management version.

    Switching to a new version_id for the same secret_id remembers the prior
    pin so ``rollback_secret_management_ref`` can restore it explicitly.
    """
    purpose = (purpose or "").strip()
    secret_id = (secret_id or "").strip()
    version_id = (version_id or "").strip()
    if not purpose or not secret_id or not version_id:
        raise ValueError("purpose, secret_id and version_id are required")

    db.execute(
        """
        INSERT INTO secret_management_refs
        (purpose, secret_id, pinned_version_id, previous_version_id, updated_at)
        VALUES (?, ?, ?, NULL, ?)
        ON CONFLICT(purpose) DO UPDATE SET
            previous_version_id = CASE
                WHEN secret_management_refs.secret_id = excluded.secret_id
                     AND secret_management_refs.pinned_version_id <> excluded.pinned_version_id
                THEN secret_management_refs.pinned_version_id
                WHEN secret_management_refs.secret_id = excluded.secret_id
                THEN secret_management_refs.previous_version_id
                ELSE NULL
            END,
            secret_id = excluded.secret_id,
            pinned_version_id = excluded.pinned_version_id,
            updated_at = excluded.updated_at
        """,
        (purpose, secret_id, version_id, _as_utc(now or utcnow())),
    )
    if hasattr(db, "commit"):
        db.commit()
    return get_secret_management_ref(db, purpose)


def get_secret_management_ref(db: Any, purpose: str) -> Optional[SecretManagementRef]:
    row = _fetch_one(
        db,
        "SELECT purpose, secret_id, pinned_version_id, previous_version_id, updated_at "
        "FROM secret_management_refs WHERE purpose = ?",
        (purpose,),
    )
    if not row:
        return None
    previous_version_id = row["previous_version_id"]
    return SecretManagementRef(
        purpose=str(row["purpose"]),
        secret_id=str(row["secret_id"]),
        pinned_version_id=str(row["pinned_version_id"]),
        previous_version_id=str(previous_version_id) if previous_version_id else None,
        updated_at=_as_utc(row["updated_at"]),
    )


def rollback_secret_management_ref(
    db: Any,
    purpose: str,
    *,
    now: Optional[datetime] = None,
) -> SecretManagementRef:
    """Swap the pinned version back to the previously pinned one, explicitly."""
    current = get_secret_management_ref(db, purpose)
    if current is None:
        raise NoActiveCredentialError(f"No secret management ref configured for '{purpose}'")
    if not current.previous_version_id:
        raise CredentialError(f"No previous version to roll back to for '{purpose}'")

    cursor = db.execute(
        """
        UPDATE secret_management_refs
        SET pinned_version_id = ?, previous_version_id = ?, updated_at = ?
        WHERE purpose = ?
          AND pinned_version_id = ?
          AND previous_version_id = ?
        """,
        (
            current.previous_version_id,
            current.pinned_version_id,
            _as_utc(now or utcnow()),
            purpose,
            current.pinned_version_id,
            current.previous_version_id,
        ),
    )
    if cursor.rowcount != 1:
        if hasattr(db, "rollback"):
            db.rollback()
        raise CredentialError(
            f"Secret management ref changed concurrently for '{purpose}'; retry rollback"
        )
    if hasattr(db, "commit"):
        db.commit()
    return get_secret_management_ref(db, purpose)


_SECRET_MANAGEMENT_CLIENT: Any = None
_SECRET_MANAGEMENT_CLIENT_LOCK = threading.Lock()


def _get_secret_management_client():
    global _SECRET_MANAGEMENT_CLIENT
    if _SECRET_MANAGEMENT_CLIENT is None:
        with _SECRET_MANAGEMENT_CLIENT_LOCK:
            if _SECRET_MANAGEMENT_CLIENT is None:
                from cloud.cloudru.secret_management import CloudRuSecretManagementClient

                _SECRET_MANAGEMENT_CLIENT = CloudRuSecretManagementClient()
    return _SECRET_MANAGEMENT_CLIENT


def resolve_secret_management_value(
    db: Any,
    purpose: str,
    client: Any = None,
) -> str:
    """Resolve one purpose's pinned Cloud.ru secret to plaintext.

    Backend-only: the caller must not log, trace, cache beyond the client's
    own bounded cache, or return this value through any client-visible path.
    There is no fallback path; if Secret Management cannot be reached this
    raises instead of reading or writing an unencrypted copy anywhere.
    """
    ref = get_secret_management_ref(db, purpose)
    if ref is None:
        raise NoActiveCredentialError(f"No secret management ref configured for '{purpose}'")
    if client is None:
        client = _get_secret_management_client()
    secret_value = client.get_secret_value(ref.secret_id, ref.pinned_version_id)
    trace = get_current_trace()
    if trace is not None:
        trace.register_sensitive_value(secret_value)
    return secret_value
