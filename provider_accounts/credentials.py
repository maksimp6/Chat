"""Provider-aware deployment credential storage and resolution.

Each provider owns an independent active credential. Plaintext exists only at
backend boundaries. Persistent records contain encrypted secrets and
non-secret provider metadata/fingerprints.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from provider_accounts.host import fingerprint_key

KEY_TTL = timedelta(hours=12)
ROTATE_BEFORE = timedelta(hours=1)
ACTIVE = "active"
ROTATING = "rotating"

YANDEX = "yandex"
CLOUDRU = "cloudru"
SUPPORTED_PROVIDERS = (YANDEX, CLOUDRU)
_HEALTH_STATUSES = {"configured", "connected", "invalid", "unavailable", "unknown"}


class CredentialError(RuntimeError):
    """Base error for provider credential failures."""


class NoActiveCredentialError(CredentialError):
    pass


class ExpiredCredentialError(CredentialError):
    pass


@dataclass(frozen=True)
class ProviderCredential:
    # Excluded from repr: a credential object that reaches a log or a traceback must not print the key.
    api_key: str = field(repr=False)
    project_id: str = ""
    provider: str = YANDEX
    id: int | None = None
    provider_key_id: str | None = None
    yandex_key_id: str | None = None
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    fingerprint: str | None = None

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
    return datetime.now(UTC)


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def create_schema(db: Any) -> None:
    db.info()


def get_active_credential(
    db: Any,
    decrypt: Callable[[str], str],
    now: datetime | None = None,
    provider: str = YANDEX,
    *,
    allow_expired: bool = False,
) -> ProviderCredential:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    identity = db.get("provider/active/" + provider)
    row = (
        db.get("provider/credential/" + str(identity)) if identity is not None else None
    )
    if row is None or row["status"] != ACTIVE:
        raise NoActiveCredentialError(f"No active {provider} provider credential")
    expires = _as_utc(row["expires_at"])
    # allow_expired lets a replacement read the superseded key's identity; it must not be used to call the provider.
    if not allow_expired and _as_utc(now or utcnow()) >= expires:
        raise ExpiredCredentialError(f"Active {provider} provider credential expired")
    secret = decrypt(row["api_key_encrypted"])
    return ProviderCredential(
        api_key=secret,
        project_id=row["project_id"],
        provider=provider,
        id=row["id"],
        provider_key_id=row["provider_key_id"],
        yandex_key_id=row["yandex_key_id"],
        issued_at=_as_utc(row["issued_at"]),
        expires_at=expires,
        fingerprint=fingerprint_key(secret),
    )


def bootstrap_credential(
    db: Any,
    api_key: str,
    project_id: str,
    encrypt: Callable[[str], str],
    now: datetime | None = None,
    decrypt: Callable[[str], str] | None = None,
    provider: str = YANDEX,
    provider_key_id: str | None = None,
    ttl: timedelta = KEY_TTL,
) -> ProviderCredential:
    if provider not in SUPPORTED_PROVIDERS or not api_key:
        raise ValueError("valid provider and nonempty api_key required")
    issued, expires = issue_window(now, ttl=ttl)
    encrypted = encrypt(api_key)
    with db.transaction() as tx:
        existing = tx.get("provider/active/" + provider)
        if existing is not None:
            if decrypt is None:
                raise NoActiveCredentialError(
                    "provider credential already bootstrapped"
                )
            current = tx.get("provider/credential/" + str(existing)) or {}
            if current.get("fingerprint") != fingerprint_key(api_key):
                # The key given here would be dropped silently; changing the key is a rotation.
                raise CredentialError(
                    "a different provider credential is already active; rotate it instead"
                )
        else:
            _publish_credential(
                tx,
                encrypted,
                project_id,
                provider,
                provider_key_id,
                issued,
                expires,
                fingerprint_key(api_key),
            )
    if existing is not None:
        # An explicit check, not assert: python -O strips asserts.
        if decrypt is None:
            raise NoActiveCredentialError("provider credential already bootstrapped")
        return get_active_credential(db, decrypt, now, provider=provider)
    return get_active_credential(db, lambda _encrypted: api_key, now, provider=provider)


def replace_active_credential(
    db: Any,
    api_key: str,
    project_id: str,
    encrypt: Callable[[str], str],
    provider: str,
    *,
    provider_key_id: str | None = None,
    now: datetime | None = None,
    ttl: timedelta = KEY_TTL,
    expected_active_id: int | None = None,
) -> ProviderCredential:
    if provider not in SUPPORTED_PROVIDERS or not api_key:
        raise ValueError("valid provider and nonempty api_key required")
    issued, expires = issue_window(now, ttl=ttl)
    encrypted = encrypt(api_key)
    with db.transaction() as tx:
        # Checked inside the transaction: a key that became active after the caller's read is never overwritten.
        if (
            expected_active_id is not None
            and tx.get("provider/active/" + provider) != expected_active_id
        ):
            raise CredentialError("Provider credential changed before replacement")
        identity = _publish_credential(
            tx,
            encrypted,
            project_id,
            provider,
            provider_key_id,
            issued,
            expires,
            fingerprint_key(api_key),
        )
    return ProviderCredential(
        api_key=api_key,
        project_id=project_id,
        provider=provider,
        id=identity,
        provider_key_id=provider_key_id,
        yandex_key_id=provider_key_id if provider == YANDEX else None,
        issued_at=issued,
        expires_at=expires,
        fingerprint=fingerprint_key(api_key),
    )


def replace_active_credentials(
    db: Any,
    replacements: list[tuple[str, str, str, str | None, str]],
    encrypt: Callable[[str], str],
    *,
    now: datetime | None = None,
    ttl: timedelta = KEY_TTL,
) -> None:
    """Store several providers' new keys and their health status in one transaction.

    Each replacement is ``(provider, api_key, project_id, provider_key_id, health_status)``.
    A failure stores none of them, so no provider is left on its new key while another
    keeps its old one.
    """
    for provider, api_key, _project_id, _key_id, status in replacements:
        if provider not in SUPPORTED_PROVIDERS or not api_key:
            raise ValueError("valid provider and nonempty api_key required")
        if status not in _HEALTH_STATUSES:
            raise ValueError("Unsupported health status")
    issued, expires = issue_window(now, ttl=ttl)
    encrypted = [encrypt(item[1]) for item in replacements]
    with db.transaction() as tx:
        for (provider, api_key, project_id, key_id, status), secret in zip(
            replacements, encrypted
        ):
            identity = _publish_credential(
                tx,
                secret,
                project_id,
                provider,
                key_id,
                issued,
                expires,
                fingerprint_key(api_key),
            )
            key = "provider/credential/" + str(identity)
            tx.set(
                key,
                {
                    **tx.get(key),
                    "last_checked_at": utcnow().isoformat(),
                    "last_check_status": status,
                    "last_check_error": None,
                },
            )


def resolve_client_credential(
    config: Any,
    db: Any = None,
    decrypt: Callable[[str], str] | None = None,
    encrypt: Callable[[str], str] | None = None,
    provider: str = YANDEX,
) -> ProviderCredential:
    if db is None:
        raise NoActiveCredentialError(
            "No native store configured for provider credentials"
        )
    if decrypt is None:
        if db.get("provider/active/" + provider) is not None:
            raise CredentialError("Provider encryption is not configured")
        raise NoActiveCredentialError(f"No global {provider} provider key configured")
    return get_active_credential(db, decrypt, provider=provider)


def list_provider_credentials(
    db: Any,
    provider: str | None = None,
) -> list[dict[str, Any]]:
    rows = [
        row
        for row in db.items(prefix="provider/credential/").values()
        # .get: a record written without the field matches no provider instead of raising KeyError.
        if provider is None or row.get("provider") == provider
    ]
    return [
        {key: value for key, value in row.items() if key != "api_key_encrypted"}
        for row in sorted(rows, key=lambda row: row["id"], reverse=True)
    ]


def rotation_needed(expires_at: datetime | str, now: datetime | None = None) -> bool:
    return _as_utc(expires_at) - _as_utc(now or utcnow()) <= ROTATE_BEFORE


def should_revoke(expires_at: datetime | str, now: datetime | None = None) -> bool:
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
    fingerprint: str | None = None,
) -> None:
    with db.transaction() as tx:
        if tx.get("provider/active/" + provider) != old_id:
            raise CredentialError("Provider credential changed during rotation")
        _publish_credential(
            tx,
            encrypted_key,
            project_id,
            provider,
            provider_key_id,
            issued_at,
            expires_at,
            fingerprint,
        )


def issue_window(
    now: datetime | None = None,
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
    error: str | None = None,
    checked_at: datetime | None = None,
) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError("Unsupported provider")
    if status not in _HEALTH_STATUSES:
        raise ValueError("Unsupported health status")
    with db.transaction() as tx:
        identity = tx.get("provider/active/" + provider)
        if identity is not None:
            key = "provider/credential/" + str(identity)
            row = tx.get(key)
            if row is None:
                return
            tx.set(
                key,
                {
                    **row,
                    "last_checked_at": _as_utc(checked_at or utcnow()).isoformat(),
                    "last_check_status": status,
                    "last_check_error": error,
                },
            )


def _publish_credential(
    tx: Any,
    encrypted: str,
    project_id: str,
    provider: str,
    key_id: str | None,
    issued: datetime,
    expires: datetime,
    fingerprint: str | None,
) -> int:
    active_key = "provider/active/" + provider
    old_id = tx.get(active_key)
    if old_id is not None:
        old_key = "provider/credential/" + str(old_id)
        old = tx.get(old_key)
        if old is not None:
            tx.set(old_key, {**old, "status": ROTATING})
    identity = int(tx.get("provider/next-id") or 0) + 1
    tx.set("provider/next-id", identity)
    tx.set(
        "provider/credential/" + str(identity),
        {
            "id": identity,
            "api_key_encrypted": encrypted,
            "project_id": project_id,
            "provider": provider,
            "provider_key_id": key_id,
            "yandex_key_id": key_id if provider == YANDEX else None,
            "issued_at": _as_utc(issued).isoformat(),
            "expires_at": _as_utc(expires).isoformat(),
            "status": ACTIVE,
            "fingerprint": fingerprint,
            "created_at": utcnow().isoformat(),
            "last_checked_at": None,
            "last_check_status": None,
            "last_check_error": None,
        },
    )
    tx.set(active_key, identity)
    return identity


def get_cloudru_iam_credentials(
    db: Any, decrypt: Callable[[str], str]
) -> dict[str, str] | None:
    row = db.get("provider/cloudru-iam")
    if row is None:
        return None
    return {
        "key_id": row["key_id"],
        "key_secret": decrypt(row["key_secret_encrypted"]),
        "project_id": row["project_id"],
        "service_account_id": row["service_account_id"] or "",
        "expires_at": row.get("expires_at") or "",
    }


def save_cloudru_iam_credentials(
    db: Any,
    *,
    key_id: str,
    key_secret: str,
    project_id: str,
    service_account_id: str | None,
    encrypt: Callable[[str], str],
    expires_at: datetime | None = None,
) -> None:
    if not key_id.strip() or not key_secret:
        raise ValueError("IAM key id and secret are required")
    if expires_at is not None and _as_utc(expires_at) <= utcnow():
        raise ValueError("IAM master key is expired")
    encrypted = encrypt(key_secret)
    with db.transaction() as tx:
        tx.set(
            "provider/cloudru-iam",
            {
                "key_id": key_id.strip(),
                "key_secret_encrypted": encrypted,
                "project_id": project_id.strip(),
                "service_account_id": service_account_id,
                "expires_at": _as_utc(expires_at).isoformat() if expires_at else None,
            },
        )
