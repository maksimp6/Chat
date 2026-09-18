"""Provider-key rotation orchestration.

The Yandex adapter is deliberately injected: deployments must supply an
implementation that creates and revokes keys using their own cloud IAM
credentials. This module owns the 12-hour lifecycle and transaction boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol, Any

from provider_credentials import KEY_TTL, issue_window, promote_rotated_key, rotation_needed, should_revoke


class YandexKeyProvider(Protocol):
    def create_key(self) -> tuple[str, str]:
        """Return (provider_key_id, plaintext_api_key)."""

    def revoke_key(self, provider_key_id: str) -> None:
        """Revoke a previously issued provider key."""


@dataclass(frozen=True)
class RotationDecision:
    rotate: bool
    revoke: bool


def decide(expires_at: datetime, now: datetime | None = None) -> RotationDecision:
    return RotationDecision(
        rotate=rotation_needed(expires_at, now),
        revoke=should_revoke(expires_at, now),
    )


def rotate_active_key(*, db: Any, provider: YandexKeyProvider,
                      encrypt: Callable[[str], str], old_id: int,
                      old_provider_key_id: str | None = None,
                      now: datetime | None = None) -> tuple[str, datetime, datetime]:
    """Create and atomically promote a fresh key.

    The caller must provide a DB adapter whose execute calls participate in one
    transaction. Plaintext is passed only to ``encrypt`` and never returned.
    """
    issued_at, expires_at = issue_window(now)
    provider_key_id, plaintext = provider.create_key()
    encrypted = encrypt(plaintext)
    promote_rotated_key(db, old_id, encrypted, provider_key_id, issued_at, expires_at)
    if old_provider_key_id:
        provider.revoke_key(old_provider_key_id)
    return provider_key_id, issued_at, expires_at
