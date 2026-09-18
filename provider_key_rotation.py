"""Provider-key rotation orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol, Any

from provider_credentials import (
    issue_window,
    promote_rotated_key,
    rotation_needed,
    should_revoke,
)


class YandexKeyProvider(Protocol):
    def create_key(self, *, expires_at: datetime) -> tuple[str, str]:
        """Return (Yandex API-key resource ID, plaintext secret)."""

    def revoke_key(self, provider_key_id: str) -> None:
        """Delete/revoke a previously issued Yandex API-key resource."""


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
    provider: YandexKeyProvider,
    encrypt: Callable[[str], str],
    old_id: int,
    project_id: str,
    old_provider_key_id: str | None = None,
    now: datetime | None = None,
) -> tuple[str, datetime, datetime]:
    """Create a fresh 12-hour key and promote it in one DB transaction.

    The database transaction must be committed by the caller. The old provider
    key is revoked only after the new database state has been promoted.
    """
    issued_at, expires_at = issue_window(now)
    provider_key_id, plaintext = provider.create_key(expires_at=expires_at)
    encrypted = encrypt(plaintext)
    promote_rotated_key(
        db,
        old_id,
        encrypted,
        provider_key_id,
        project_id,
        issued_at,
        expires_at,
    )
    if old_provider_key_id:
        provider.revoke_key(old_provider_key_id)
    return provider_key_id, issued_at, expires_at
