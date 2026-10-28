"""Generic cryptography and secret-key lifecycle manager for Logos.

Secrets are encrypted at rest and are addressed by an opaque key reference.
Plaintext values exist only during an explicit read/write operation and are
never returned by metadata/list operations.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from secret_store.credential_crypto import fingerprint_key
from secret_store.host import get_store

DatabaseRow = dict[str, Any]


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
    expires_at: str | None
    rotated_from: str | None
    fingerprint: str


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _manager_key() -> bytes:
    raw = (
        os.environ.get("LOGOS_KEY_MANAGER_KEY")
        or os.environ.get("LOGOS_PROVIDER_CREDENTIAL_KEY")
        or ""
    ).strip()
    if not raw:
        raise KeyManagerError(
            "Set LOGOS_KEY_MANAGER_KEY (or LOGOS_PROVIDER_CREDENTIAL_KEY) to a Fernet key"
        )
    try:
        return raw.encode("ascii")
    except UnicodeEncodeError as exc:
        raise KeyManagerError(
            "Key-manager encryption key must be ASCII Fernet material"
        ) from exc


def _fernet() -> Fernet:
    try:
        return Fernet(_manager_key())
    except Exception as exc:
        raise KeyManagerError("Invalid key-manager encryption key") from exc


def secret_storage_ready() -> bool:
    """True when managed secrets can be encrypted; checked before an issued key is stored."""
    try:
        _fernet()
    except KeyManagerError:
        return False
    return True


def create_schema(db: Any) -> None:
    db.info()


def _ensure_schema() -> None:
    get_store()


def _get_row(key_ref: str) -> DatabaseRow:
    row = get_store().get("key-manager/" + key_ref)
    if row is None:
        raise KeyNotFoundError("Managed key not found")
    return row


def _check_active(row: DatabaseRow) -> None:
    status = row["status"]
    if status == "revoked":
        raise KeyRevokedError(f"Key '{row['key_ref']}' is revoked")
    if status != "active":
        raise KeyManagerError(f"Key '{row['key_ref']}' is not active")
    expires_at = row["expires_at"]
    if expires_at:
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if datetime.now(UTC) >= expires:
            raise KeyExpiredError(f"Key '{row['key_ref']}' is expired")


def _new_row(
    *,
    secret: str,
    name: str,
    purpose: str,
    provider: str,
    environment: str,
    owner: str,
    expires_at: datetime | None,
    key_ref: str | None,
    rotated_from: str | None,
) -> DatabaseRow:
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
    created_at = _iso(datetime.now(UTC))
    expires_value = _iso(expires_at) if expires_at else None
    encrypted = _fernet().encrypt(secret.encode("utf-8")).decode("ascii")

    row = {
        "key_ref": ref,
        "name": name.strip(),
        "purpose": purpose.strip(),
        "provider": provider.strip(),
        "environment": environment.strip(),
        "owner": owner.strip(),
        "encrypted_secret": encrypted,
        "status": "active",
        "created_at": created_at,
        "expires_at": expires_value,
        "rotated_from": rotated_from,
        "fingerprint": fingerprint_key(secret),
        "revoked_at": None,
    }
    return row


def store_secret(
    *,
    secret: str,
    name: str,
    purpose: str,
    provider: str,
    environment: str = "production",
    owner: str = "",
    expires_at: datetime | None = None,
    key_ref: str | None = None,
    rotated_from: str | None = None,
) -> KeyMetadata:
    row = _new_row(
        secret=secret,
        name=name,
        purpose=purpose,
        provider=provider,
        environment=environment,
        owner=owner,
        expires_at=expires_at,
        key_ref=key_ref,
        rotated_from=rotated_from,
    )
    with get_store().transaction() as tx:
        key = "key-manager/" + row["key_ref"]
        if tx.get(key) is not None:
            raise KeyManagerError("Managed key reference already exists")
        tx.set(key, row)
    return _metadata(row)


def read_secret(key_ref: str) -> str:
    row = _get_row(key_ref)
    _check_active(row)
    try:
        return (
            _fernet().decrypt(row["encrypted_secret"].encode("ascii")).decode("utf-8")
        )
    except InvalidToken as exc:
        raise KeyManagerError("Unable to decrypt managed secret") from exc


def get_metadata(key_ref: str) -> KeyMetadata:
    return _metadata(_get_row(key_ref))


def list_keys(
    *,
    provider: str | None = None,
    purpose: str | None = None,
    environment: str | None = None,
    include_revoked: bool = False,
) -> list[KeyMetadata]:
    rows = get_store().items(prefix="key-manager/").values()
    selected = [
        row
        for row in rows
        if (not provider or row["provider"] == provider)
        and (not purpose or row["purpose"] == purpose)
        and (not environment or row["environment"] == environment)
        and (include_revoked or row["status"] != "revoked")
    ]
    return [
        _metadata(row)
        for row in sorted(selected, key=lambda row: row["created_at"], reverse=True)
    ]


def revoke_key(key_ref: str) -> None:
    key = "key-manager/" + key_ref
    with get_store().transaction() as tx:
        row = tx.get(key)
        if row is None:
            raise KeyNotFoundError("Managed key not found")
        if row["status"] != "revoked":
            tx.set(
                key,
                {
                    **row,
                    "status": "revoked",
                    "revoked_at": _iso(datetime.now(UTC)),
                },
            )


def delete_key(key_ref: str) -> None:
    key = "key-manager/" + key_ref
    with get_store().transaction() as tx:
        row = tx.get(key)
        if row is None:
            raise KeyNotFoundError("Managed key not found")
        if row["status"] != "revoked":
            raise KeyManagerError("Only revoked keys can be permanently deleted")
        tx.delete(key)


def rotate_key(
    key_ref: str,
    *,
    secret: str,
    expires_at: datetime | None = None,
    name: str | None = None,
) -> KeyMetadata:
    old = _get_row(key_ref)
    _check_active(old)
    row = _new_row(
        secret=secret,
        name=name or old["name"],
        purpose=old["purpose"],
        provider=old["provider"],
        environment=old["environment"],
        owner=old["owner"],
        expires_at=expires_at,
        key_ref=None,
        rotated_from=key_ref,
    )
    key = "key-manager/" + key_ref
    # Successor and revocation commit together, and the old key is checked
    # again inside: a parallel rotation finds it revoked instead of creating
    # a second active successor.
    with get_store().transaction() as tx:
        current = tx.get(key)
        if current is None:
            raise KeyNotFoundError("Managed key not found")
        _check_active(current)
        new_key = "key-manager/" + row["key_ref"]
        if tx.get(new_key) is not None:
            raise KeyManagerError("Managed key reference already exists")
        tx.set(new_key, row)
        tx.set(
            key,
            {
                **current,
                "status": "revoked",
                "revoked_at": _iso(datetime.now(UTC)),
            },
        )
    return _metadata(row)


def _metadata(row: DatabaseRow) -> KeyMetadata:
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
    "KeyExpiredError",
    "KeyManagerError",
    "KeyMetadata",
    "KeyNotFoundError",
    "KeyRevokedError",
    "create_schema",
    "delete_key",
    "get_metadata",
    "list_keys",
    "read_secret",
    "revoke_key",
    "rotate_key",
    "store_secret",
]
