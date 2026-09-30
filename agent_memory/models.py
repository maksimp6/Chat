"""Immutable shared-agent memory records with provenance and freshness metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping
import re
import time
import uuid

from trace_security import sanitize_trace_value


MEMORY_KINDS = frozenset({"task", "project", "role", "process", "user_status"})
MEMORY_STATUSES = frozenset({"active", "stale", "superseded"})
SENSITIVITY_LEVELS = frozenset({"public", "internal", "sensitive"})
_MEMORY_SECRET_KEYS = frozenset(
    {
        "private_key",
        "privatekey",
        "ssh_private_key",
        "secret_key",
        "client_secret",
    }
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [^-\n]*PRIVATE KEY-----.*?-----END [^-\n]*PRIVATE KEY-----",
    re.DOTALL,
)


def _sanitize_memory_value(value: Any) -> Any:
    sanitized = sanitize_trace_value(value)
    if isinstance(sanitized, str):
        return _PRIVATE_KEY_BLOCK.sub("<redacted-private-key>", sanitized)
    if isinstance(sanitized, dict):
        result = {}
        for key, item in sanitized.items():
            normalized = str(key).lower().replace("-", "_")
            result[str(key)] = (
                "<redacted>"
                if normalized in _MEMORY_SECRET_KEYS
                else _sanitize_memory_value(item)
            )
        return result
    if isinstance(sanitized, (list, tuple, set)):
        return [_sanitize_memory_value(item) for item in sanitized]
    return sanitized


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(_sanitize_memory_value(str(value)))


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class MemoryRecord:
    """One reusable memory item.

    The record stores a compact derived fact plus authoritative provenance. It is
    never a replacement for GitHub, Execution Trace, or another source of truth.
    """

    memory_id: str
    kind: str
    scope: str
    text: str
    provenance: Mapping[str, Any]
    source_version: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    freshness: Mapping[str, Any] = field(
        default_factory=lambda: {"invalidate_on": ["source_version_change"]}
    )
    sensitivity: str = "internal"
    visibility: tuple[str, ...] = ()
    status: str = "active"
    created_at: int = 0
    updated_at: int = 0
    expires_at: int | None = None
    superseded_by: str | None = None

    def __post_init__(self) -> None:
        for name in ("memory_id", "kind", "scope", "text", "source_version"):
            value = _clean_text(getattr(self, name)).strip()
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value)

        if self.kind not in MEMORY_KINDS:
            raise ValueError(f"unsupported memory kind: {self.kind}")
        if self.status not in MEMORY_STATUSES:
            raise ValueError(f"unsupported memory status: {self.status}")
        if self.sensitivity not in SENSITIVITY_LEVELS:
            raise ValueError(f"unsupported sensitivity: {self.sensitivity}")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", float(self.confidence))

        provenance = _sanitize_memory_value(dict(self.provenance))
        source_type = str(provenance.get("source_type") or "").strip()
        refs = provenance.get("refs")
        if not source_type or not isinstance(refs, list | tuple) or not refs:
            raise ValueError("provenance requires source_type and non-empty refs")
        if source_type == "github" and not str(provenance.get("repository") or "").strip():
            raise ValueError("GitHub provenance requires repository")
        object.__setattr__(self, "provenance", _freeze(provenance))

        payload = _sanitize_memory_value(dict(self.payload))
        freshness = _sanitize_memory_value(dict(self.freshness))
        invalidate_on = freshness.get("invalidate_on")
        if not isinstance(invalidate_on, list | tuple) or not invalidate_on:
            raise ValueError("freshness requires non-empty invalidate_on")
        object.__setattr__(self, "payload", _freeze(payload))
        object.__setattr__(self, "freshness", _freeze(freshness))
        object.__setattr__(
            self,
            "visibility",
            tuple(sorted(set(_clean_text(item).strip() for item in self.visibility if item))),
        )

        for name in ("created_at", "updated_at"):
            value = int(getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)

        if self.expires_at is not None:
            expires_at = int(self.expires_at)
            if expires_at < 0:
                raise ValueError("expires_at must be non-negative")
            object.__setattr__(self, "expires_at", expires_at)
        if self.superseded_by is not None:
            object.__setattr__(
                self, "superseded_by", _clean_text(self.superseded_by).strip() or None
            )

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        scope: str,
        text: str,
        provenance: Mapping[str, Any],
        source_version: str,
        memory_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
        confidence: float = 1.0,
        freshness: Mapping[str, Any] | None = None,
        sensitivity: str = "internal",
        visibility: tuple[str, ...] = (),
        expires_at: int | None = None,
        now: int | None = None,
    ) -> "MemoryRecord":
        timestamp = int(time.time()) if now is None else int(now)
        return cls(
            memory_id=memory_id or str(uuid.uuid4()),
            kind=kind,
            scope=scope,
            text=text,
            provenance=provenance,
            source_version=source_version,
            payload=payload or {},
            confidence=confidence,
            freshness=freshness or {"invalidate_on": ["source_version_change"]},
            sensitivity=sensitivity,
            visibility=visibility,
            created_at=timestamp,
            updated_at=timestamp,
            expires_at=expires_at,
        )

    def is_expired(self, *, now: int | None = None) -> bool:
        if self.expires_at is None:
            return False
        timestamp = int(time.time()) if now is None else int(now)
        return timestamp >= self.expires_at

    def as_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "kind": self.kind,
            "scope": self.scope,
            "text": self.text,
            "provenance": _thaw(self.provenance),
            "source_version": self.source_version,
            "payload": _thaw(self.payload),
            "confidence": self.confidence,
            "freshness": _thaw(self.freshness),
            "sensitivity": self.sensitivity,
            "visibility": list(self.visibility),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "superseded_by": self.superseded_by,
        }


@dataclass(frozen=True)
class MemoryLookup:
    """Freshness-aware lookup result used by later retrieval/RAG layers."""

    status: str
    memory_id: str
    record: MemoryRecord | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"hit", "miss", "stale", "superseded"}:
            raise ValueError("unsupported memory lookup status")
