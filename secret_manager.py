"""Alias, policy and audit boundary for Alice Secrets.

This module never stores or returns plaintext. It maps user-friendly aliases to
canonical SecretRef metadata and authorizes a runtime purpose before resolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Protocol

from secret_store.core import SecretRef, SecretResolver, SecretValue

_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


class SecretAliasError(Exception):
    """Safe alias/policy error without secret values."""


@dataclass(frozen=True, slots=True)
class SecretAlias:
    alias: str
    ref: SecretRef
    allowed_purposes: frozenset[str]

    def __post_init__(self) -> None:
        if not _ALIAS_RE.fullmatch(self.alias):
            raise ValueError("invalid secret alias")
        if not self.allowed_purposes:
            raise ValueError("at least one allowed purpose is required")


@dataclass(frozen=True, slots=True)
class SecretAccessEvent:
    alias: str
    purpose: str
    operation: str
    success: bool
    occurred_at: str


class SecretAliasStore(Protocol):
    def get(self, alias: str) -> SecretAlias | None: ...

    def put(self, entry: SecretAlias) -> None: ...

    def delete(self, alias: str) -> None: ...

    def list(self) -> tuple[SecretAlias, ...]: ...


class InMemorySecretAliasStore:
    """Deterministic metadata-only store used until #776 persistence wiring."""

    def __init__(self) -> None:
        self._entries: dict[str, SecretAlias] = {}

    def get(self, alias: str) -> SecretAlias | None:
        return self._entries.get(alias)

    def put(self, entry: SecretAlias) -> None:
        self._entries[entry.alias] = entry

    def delete(self, alias: str) -> None:
        self._entries.pop(alias, None)

    def list(self) -> tuple[SecretAlias, ...]:
        return tuple(self._entries[key] for key in sorted(self._entries))


class SecretManager:
    def __init__(self, store: SecretAliasStore, resolver: SecretResolver) -> None:
        self._store = store
        self._resolver = resolver
        self._audit: list[SecretAccessEvent] = []

    def put_alias(self, entry: SecretAlias) -> None:
        self._store.put(entry)

    def delete_alias(self, alias: str) -> None:
        self._store.delete(alias)

    def list_aliases(self) -> tuple[SecretAlias, ...]:
        return self._store.list()

    def use(self, alias: str, purpose: str) -> SecretValue:
        entry = self._store.get(alias)
        if entry is None:
            self._record(alias, purpose, "use", False)
            raise SecretAliasError("secret alias not found")
        if purpose not in entry.allowed_purposes:
            self._record(alias, purpose, "use", False)
            raise SecretAliasError("secret purpose is not allowed")
        try:
            value = self._resolver.resolve(entry.ref)
        except Exception:
            self._record(alias, purpose, "use", False)
            raise
        self._record(alias, purpose, "use", True)
        return value

    def audit_events(self) -> tuple[SecretAccessEvent, ...]:
        return tuple(self._audit)

    def _record(self, alias: str, purpose: str, operation: str, success: bool) -> None:
        self._audit.append(
            SecretAccessEvent(
                alias=alias,
                purpose=purpose,
                operation=operation,
                success=success,
                occurred_at=datetime.now(timezone.utc).isoformat(),
            )
        )


__all__ = [
    "InMemorySecretAliasStore",
    "SecretAccessEvent",
    "SecretAlias",
    "SecretAliasError",
    "SecretAliasStore",
    "SecretManager",
]
