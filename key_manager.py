"""Generic cryptography and secret-key lifecycle manager for Alice Pro.

Secrets are encrypted at rest and are addressed by an opaque key reference.
Plaintext values exist only during an explicit read/write operation and are
never returned by metadata/list operations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
import sqlite3
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken


class KeyManagerError(RuntimeError):
    """Base error for key-manager failures."""


class KeyNotFoundError(KeyManagerError):
    """Requested key reference does not exist."""


class KeyExpiredError(KeyManagerError):
    """Requested key exists but is expired."""


class KeyRevokedError(KeyManagerError):
    """Requested key was revoked."""


@dataclass(frozen=True)
class KeyMetadata:
    key_ref: str
    name: str
    purpose: str
    provider: str
    environment: str
    owner: str
    status: str
    created_at: str
    expires_at: Optional[str]
    rotated_from: Optional[str]
    fingerprint: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _manager_key() -> bytes:
    raw = (
        os.getenv("ALICE_KEY_MANAGER_KEY")
        or os.getenv("ALICE_PROVIDER_CREDENTIAL_KEY")
        or ""
    ).strip()
    if not raw:
        raise KeyManagerError(
            "Set ALICE_KEY_MANAGER_KEY (or ALICE_PROVIDER_CREDENTIAL_KEY) to a Fernet key"
        )
    try:
        return raw.encode("ascii")
    except UnicodeEncodeError as exc:
        raise KeyManagerError("Key-manager encryption key must be ASCII Fernet material") from exc


def _fernet() -> Fernet:
    try:
        return Fernet(_manager_key())
    except Exception as exc:
        raise KeyManagerError("Invalid key-manager encryption key") from exc


def _fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def create_schema(db: Any) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS managed_keys (
            key_ref TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            purpose TEXT NOT NULL,
            provider TEXT NOT NULL,
            environment TEXT NOT NULL,
            owner TEXT NOT NULL DEFAULT '',
            encrypted_secret TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            expires_at TEXT,
            revoked_at TEXT,
            rotated_from TEXT,
            fingerprint TEXT NOT NULL
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_managed_keys_lookup "
        "ON managed_keys(provider, purpose, environment, status)"
    )


def _ensure_schema() -> None:
    from db import get_conn

    conn = get_conn()
    try:
        create_schema(conn)
        conn.commit()
    finally:
        conn.close()


def _get_row(key_ref: str) -> sqlite3.Row:
    from db import get_conn

    _ensure_schema()
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM managed_keys WHERE key_ref = ?",
            (key_ref,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise KeyNotFoundError(f"Key '{key_ref}' not found")
    return row


def _check_active(row: sqlite3.Row) -> None:
    status = row["status"]
    if status == "revoked":
        raise KeyRevokedError(f"Key '{row['key_ref']}' is revoked")
    if status != "active":
        raise KeyManagerError(f"Key '{row['key_ref']}' is not active")
    expires_at = row["expires_at"]
    if expires_at:
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if _utcnow() >= expires:
            raise KeyExpiredError(f"Key '{row['key_ref']}' is expired")


def store_secret(
    *,
    secret: str,
    name: str,
    purpose: str,
    provider: str,
    environment: str = "production",
    owner: str = "",
    expires_at: Optional[datetime] = None,
    key_ref: Optional[str] = None,
    rotated_from: Optional[str] = None,
) -> KeyMetadata:
    if not isinstance(secret, str) or not secret:
        raise ValueError("secret must be a non-empty string")
    for field_name, value in (
        ("name", name),
        ("purpose", purpose),
        ("provider", provider),
        ("environment", environment),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")

    _ensure_schema()
    ref = key_ref or f"key_{os.urandom(16).hex()}"
    created_at = _iso(_utcnow())
    expires_value = _iso(expires_at) if expires_at else None
    encrypted = _fernet().encrypt(secret.encode("utf-8")).decode("ascii")

    from db import get_conn

    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO managed_keys
            (key_ref, name, purpose, provider, environment, owner,
             encrypted_secret, status, created_at, expires_at, rotated_from, fingerprint)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
            """,
            (
                ref,
                name.strip(),
                purpose.strip(),
                provider.strip(),
                environment.strip(),
                owner.strip(),
                encrypted,
                created_at,
                expires_value,
                rotated_from,
                _fingerprint(secret),
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM managed_keys WHERE key_ref = ?", (ref,)
        ).fetchone()
    finally:
        conn.close()
    return _metadata(row)


def read_secret(key_ref: str) -> str:
    row = _get_row(key_ref)
    _check_active(row)
    try:
        return _fernet().decrypt(row["encrypted_secret"].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise KeyManagerError("Unable to decrypt managed secret") from exc


def get_metadata(key_ref: str) -> KeyMetadata:
    return _metadata(_get_row(key_ref))


def list_keys(
    *,
    provider: Optional[str] = None,
    purpose: Optional[str] = None,
    environment: Optional[str] = None,
    include_revoked: bool = False,
) -> list[KeyMetadata]:
    _ensure_schema()
    clauses = []
    params = []
    if provider:
        clauses.append("provider = ?")
        params.append(provider)
    if purpose:
        clauses.append("purpose = ?")
        params.append(purpose)
    if environment:
        clauses.append("environment = ?")
        params.append(environment)
    if not include_revoked:
        clauses.append("status != 'revoked'")

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    from db import get_conn

    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM managed_keys" + where + " ORDER BY created_at DESC",
            params,
        ).fetchall()
    finally:
        conn.close()
    return [_metadata(row) for row in rows]


def revoke_key(key_ref: str) -> None:
    row = _get_row(key_ref)
    if row["status"] == "revoked":
        return
    from db import get_conn

    conn = get_conn()
    try:
        conn.execute(
            "UPDATE managed_keys SET status = 'revoked', revoked_at = ? WHERE key_ref = ?",
            (_iso(_utcnow()), key_ref),
        )
        conn.commit()
    finally:
        conn.close()


def rotate_key(
    key_ref: str,
    *,
    secret: str,
    expires_at: Optional[datetime] = None,
    name: Optional[str] = None,
) -> KeyMetadata:
    old = _get_row(key_ref)
    _check_active(old)
    new_meta = store_secret(
        secret=secret,
        name=name or old["name"],
        purpose=old["purpose"],
        provider=old["provider"],
        environment=old["environment"],
        owner=old["owner"],
        expires_at=expires_at,
        rotated_from=key_ref,
    )
    revoke_key(key_ref)
    return new_meta


def _metadata(row: sqlite3.Row) -> KeyMetadata:
    return KeyMetadata(
        key_ref=str(row["key_ref"]),
        name=str(row["name"]),
        purpose=str(row["purpose"]),
        provider=str(row["provider"]),
        environment=str(row["environment"]),
        owner=str(row["owner"] or ""),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        expires_at=row["expires_at"],
        rotated_from=row["rotated_from"],
        fingerprint=str(row["fingerprint"]),
    )


__all__ = [
    "KeyManagerError",
    "KeyNotFoundError",
    "KeyExpiredError",
    "KeyRevokedError",
    "KeyMetadata",
    "create_schema",
    "store_secret",
    "read_secret",
    "get_metadata",
    "list_keys",
    "revoke_key",
    "rotate_key",
]
