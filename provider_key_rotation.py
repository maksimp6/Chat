"""Provider-agnostic API-key rotation orchestration.

Providers may either create a replacement resource (Yandex) or reissue the
existing resource in place (Cloud.ru). Both paths validate the new secret
before promotion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Protocol, Any

from provider_credentials import (
    fingerprint_key,
    issue_window,
    promote_rotated_key,
    rotation_needed,
    should_revoke,
    record_health_check,
)


class ProviderKeyProvider(Protocol):
    def create_key(self, *, expires_at: datetime) -> tuple[str, str]:
        """Return (provider resource ID, plaintext secret)."""

    def validate_key(self, api_key: str) -> None:
        """Raise when the key cannot perform a real provider request."""

    def revoke_key(self, provider_key_id: str) -> None:
        """Revoke a previously issued provider key."""

    def reissue_key(self, provider_key_id: str, *, expires_at: datetime) -> tuple[str, str]:
        """Reissue an existing provider resource when supported."""


# Backwards-compatible alias for existing Yandex integrations/tests.
YandexKeyProvider = ProviderKeyProvider


@dataclass(frozen=True)
class RotationDecision:
    rotate: bool
    revoke: bool


def decide(expires_at: datetime, now: datetime | None = None) -> RotationDecision:
    return RotationDecision(
        rotate=rotation_needed(expires_at, now),
        revoke=should_revoke(expires_at, now),
    )


def rotate_active_key(
    *,
    db: Any,
    provider: ProviderKeyProvider,
    encrypt: Callable[[str], str],
    old_id: int,
    project_id: str,
    old_provider_key_id: str | None = None,
    now: datetime | None = None,
    commit_before_revoke: bool = False,
    provider_name: str = "yandex",
    reissue_existing: bool = False,
    ttl: timedelta | None = None,
) -> tuple[str, datetime, datetime]:
    """Create/reissue, validate and promote one provider credential.

    Yandex creates a new key and then revokes the old resource.
    Cloud.ru reissues the current key in place; its resource ID is intentionally
    unchanged, so revoking the old ID would also revoke the replacement.
    """
    issued_at, expires_at = issue_window(now, ttl=ttl) if ttl is not None else issue_window(now)

    if reissue_existing:
        if not old_provider_key_id:
            raise RuntimeError("Provider key ID is required for in-place reissue")
        reissuer = getattr(provider, "reissue_key", None)
        if not callable(reissuer):
            raise RuntimeError(f"{provider_name} does not support key reissue")
        provider_key_id, plaintext = reissuer(
            old_provider_key_id,
            expires_at=expires_at,
        )
        if str(provider_key_id) != str(old_provider_key_id):
            raise RuntimeError(f"{provider_name} reissue changed the provider key ID unexpectedly")
        provider.validate_key(plaintext)
        encrypted = encrypt(plaintext)
        promote_rotated_key(
            db,
            old_id,
            encrypted,
            provider_key_id,
            project_id,
            issued_at,
            expires_at,
            provider=provider_name,
            fingerprint=fingerprint_key(plaintext),
        )
        record_health_check(
            db,
            provider_name,
            status="connected",
            error=None,
            commit=False,
        )
        if commit_before_revoke and hasattr(db, "commit"):
            db.commit()
        return provider_key_id, issued_at, expires_at

    provider_key_id, plaintext = provider.create_key(expires_at=expires_at)
    provider.validate_key(plaintext)
    encrypted = encrypt(plaintext)
    promote_rotated_key(
        db,
        old_id,
        encrypted,
        provider_key_id,
        project_id,
        issued_at,
        expires_at,
        provider=provider_name,
        fingerprint=fingerprint_key(plaintext),
    )
    record_health_check(
        db,
        provider_name,
        status="connected",
        error=None,
        commit=False,
    )
    if commit_before_revoke and hasattr(db, "commit"):
        db.commit()
    if old_provider_key_id:
        provider.revoke_key(old_provider_key_id)
    return provider_key_id, issued_at, expires_at
