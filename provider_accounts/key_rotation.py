"""Provider-agnostic API-key rotation orchestration.

Providers may either create a replacement resource (Yandex) or reissue the
existing resource in place (Cloud.ru). Both paths validate the new secret
before promotion.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Protocol, Any

from provider_accounts.credentials import (
    fingerprint_key,
    issue_window,
    promote_rotated_key,
    rotation_needed,
    should_revoke,
    record_health_check,
)

logger = logging.getLogger("logos.provider_accounts.key_rotation")


class ProviderKeyProvider(Protocol):
    def create_key(self, *, expires_at: datetime) -> tuple[str, str]:
        """Return (provider resource ID, plaintext secret)."""

    def validate_key(self, api_key: str) -> None:
        """Raise when the key cannot perform a real provider request."""

    def revoke_key(self, provider_key_id: str) -> None:
        """Revoke a previously issued provider key."""

    def reissue_key(
        self, provider_key_id: str, *, expires_at: datetime
    ) -> tuple[str, str]:
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


def _revoke_quietly(provider: ProviderKeyProvider, provider_key_id: str) -> None:
    """Revoke a key that was created but never stored, so it is not left active at the provider."""
    try:
        provider.revoke_key(provider_key_id)
    except Exception:
        logger.exception("Could not revoke an unstored provider key")


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
    issued_at, expires_at = (
        issue_window(now, ttl=ttl) if ttl is not None else issue_window(now)
    )

    if reissue_existing:
        if not old_provider_key_id:
            raise RuntimeError("Provider key ID is required for in-place reissue")
        reissuer = getattr(provider, "reissue_key", None)
        if not callable(reissuer):
            raise RuntimeError(f"{provider_name} does not support key reissue")
        # Checked before the provider changes its secret: a reissue that cannot be stored would
        # leave the provider holding a key that nothing in Logos has.
        if db.get("provider/active/" + provider_name) != old_id:
            raise RuntimeError("Provider credential changed during rotation")
        provider_key_id, plaintext = reissuer(
            old_provider_key_id,
            expires_at=expires_at,
        )
        # Stored before any check that can fail: the provider has already replaced its secret.
        promote_rotated_key(
            db,
            old_id,
            encrypt(plaintext),
            provider_key_id,
            project_id,
            issued_at,
            expires_at,
            provider=provider_name,
            fingerprint=fingerprint_key(plaintext),
        )
        try:
            provider.validate_key(plaintext)
        except Exception:
            record_health_check(
                db,
                provider_name,
                status="invalid",
                error="reissued key failed validation",
            )
            raise
        record_health_check(
            db,
            provider_name,
            status="connected",
            error=None,
        )
        # Native promotion already passed the synchronous durability barrier.
        return provider_key_id, issued_at, expires_at

    provider_key_id, plaintext = provider.create_key(expires_at=expires_at)
    try:
        provider.validate_key(plaintext)
        promote_rotated_key(
            db,
            old_id,
            encrypt(plaintext),
            provider_key_id,
            project_id,
            issued_at,
            expires_at,
            provider=provider_name,
            fingerprint=fingerprint_key(plaintext),
        )
    except Exception:
        _revoke_quietly(provider, provider_key_id)
        raise
    record_health_check(
        db,
        provider_name,
        status="connected",
        error=None,
    )
    # Native promotion already passed the synchronous durability barrier.
    if old_provider_key_id:
        try:
            provider.revoke_key(old_provider_key_id)
        except Exception:
            # The new key is promoted and durable, so a failed revoke must not fail the rotation.
            # The superseded id is logged so it can be revoked by hand.
            logger.exception(
                "Could not revoke the superseded provider key %s", old_provider_key_id
            )
    return provider_key_id, issued_at, expires_at
