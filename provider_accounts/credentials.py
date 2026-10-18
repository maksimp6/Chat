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

from observability.trace_manager import get_current_trace

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


def create_schema(db: Any) -> None:
    db.info()


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


def get_active_credential(
    db: Any,
    decrypt: Callable[[str], str],
    now: Optional[datetime] = None,
    provider: str = YANDEX,
) -> ProviderCredential:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    identity = db.get("provider/active/" + provider)
    row = db.get("provider/credential/" + str(identity)) if identity is not None else None
    if row is None or row["status"] != ACTIVE:
        raise NoActiveCredentialError(f"No active {provider} provider credential")
    expires = _as_utc(row["expires_at"])
    if _as_utc(now or utcnow()) >= expires:
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
    if provider not in SUPPORTED_PROVIDERS or not api_key:
        raise ValueError("valid provider and nonempty api_key required")
    issued, expires = issue_window(now, ttl=ttl)
    encrypted = encrypt(api_key)
    with db.transaction() as tx:
        existing = tx.get("provider/active/" + provider)
        if existing is not None:
            if decrypt is None:
                raise NoActiveCredentialError("provider credential already bootstrapped")
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
        assert decrypt is not None
        return get_active_credential(db, decrypt, now, provider=provider)
    return get_active_credential(db, lambda _encrypted: api_key, now, provider=provider)


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
    if provider not in SUPPORTED_PROVIDERS or not api_key:
        raise ValueError("valid provider and nonempty api_key required")
    issued, expires = issue_window(now, ttl=ttl)
    encrypted = encrypt(api_key)
    with db.transaction() as tx:
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


def resolve_client_credential(
    config: Any,
    db: Any = None,
    decrypt: Optional[Callable[[str], str]] = None,
    encrypt: Optional[Callable[[str], str]] = None,
    provider: str = YANDEX,
) -> ProviderCredential:
    if db is None:
        raise NoActiveCredentialError("No native store configured for provider credentials")
    if decrypt is None:
        if db.get("provider/active/" + provider) is not None:
            raise CredentialError("Provider encryption is not configured")
        raise NoActiveCredentialError(f"No global {provider} provider key configured")
    return get_active_credential(db, decrypt, provider=provider)


def list_provider_credentials(
    db: Any,
    provider: Optional[str] = None,
) -> list[dict[str, Any]]:
    rows = [
        row
        for row in db.items(prefix="provider/credential/").values()
        if provider is None or row["provider"] == provider
    ]
    return [
        {key: value for key, value in row.items() if key != "api_key_encrypted"}
        for row in sorted(rows, key=lambda row: row["id"], reverse=True)
    ]


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
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError("Unsupported provider")
    if status not in {"configured", "connected", "invalid", "unavailable", "unknown"}:
        raise ValueError("Unsupported health status")
    with db.transaction() as tx:
        identity = tx.get("provider/active/" + provider)
        if identity is not None:
            key = "provider/credential/" + str(identity)
            row = tx.get(key)
            tx.set(
                key,
                {
                    **row,
                    "last_checked_at": _as_utc(checked_at or utcnow()).isoformat(),
                    "last_check_status": status,
                    "last_check_error": error,
                },
            )


@dataclass(frozen=True)
class SecretManagementRef:
    """A local pointer to one immutable Cloud.ru Secret Management version.

    Cloud.ru versions cannot be edited in place, so "rotate" and "rollback"
    are purely local: which version_id this app currently trusts for a given
    purpose (e.g. ``alice_short_token``, ``github_oauth_client_secret``,
    ``provider_credential:yandex``). ``alice_database_url`` is retired; see
    ``docs/production-deployment.md``. The value itself is never stored here;
    it is resolved on demand through ``resolve_secret_management_value``.
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
    purpose, secret_id, version_id = purpose.strip(), secret_id.strip(), version_id.strip()
    if not purpose or not secret_id or not version_id:
        raise ValueError("purpose, secret_id and version_id are required")
    key = "secret/ref/" + purpose
    with db.transaction() as tx:
        old = tx.get(key)
        previous = None
        if old and old["secret_id"] == secret_id:
            previous = (
                old["pinned_version_id"]
                if old["pinned_version_id"] != version_id
                else old["previous_version_id"]
            )
        row = {
            "purpose": purpose,
            "secret_id": secret_id,
            "pinned_version_id": version_id,
            "previous_version_id": previous,
            "updated_at": _as_utc(now or utcnow()).isoformat(),
        }
        tx.set(key, row)
    return _ref_from_record(row)


def get_secret_management_ref(db: Any, purpose: str) -> Optional[SecretManagementRef]:
    row = db.get("secret/ref/" + purpose)
    return _ref_from_record(row) if row else None


def rollback_secret_management_ref(
    db: Any,
    purpose: str,
    *,
    now: Optional[datetime] = None,
) -> SecretManagementRef:
    key = "secret/ref/" + purpose
    with db.transaction() as tx:
        row = tx.get(key)
        if row is None:
            raise NoActiveCredentialError("No secret management ref configured")
        if not row["previous_version_id"]:
            raise CredentialError("No previous secret version")
        row.update(
            pinned_version_id=row["previous_version_id"],
            previous_version_id=row["pinned_version_id"],
            updated_at=_as_utc(now or utcnow()).isoformat(),
        )
        tx.set(key, row)
    return _ref_from_record(row)


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


def get_cloudru_iam_credentials(db: Any, decrypt: Callable[[str], str]) -> Optional[dict[str, str]]:
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
    service_account_id: Optional[str],
    encrypt: Callable[[str], str],
    expires_at: Optional[datetime] = None,
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


def _ref_from_record(row: dict[str, Any]) -> SecretManagementRef:
    return SecretManagementRef(
        purpose=str(row["purpose"]),
        secret_id=str(row["secret_id"]),
        pinned_version_id=str(row["pinned_version_id"]),
        previous_version_id=str(row["previous_version_id"]) if row["previous_version_id"] else None,
        updated_at=_as_utc(row["updated_at"]),
    )
