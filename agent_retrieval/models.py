"""Immutable retrieval contracts with source attribution and strict budgets."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from trace_security import sanitize_trace_value


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(sanitize_trace_value(str(value)))


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
class RetrievalQuery:
    text: str
    repository: str
    head_sha: str
    role: str | None = None
    memory_scope: str | None = None
    source_types: tuple[str, ...] = ()
    memory_kinds: tuple[str, ...] = ()
    include_sensitive: bool = False
    limit: int = 8
    max_chars: int = 12000

    def __post_init__(self) -> None:
        for name in ("text", "repository", "head_sha"):
            value = _clean(getattr(self, name)).strip()
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value)
        if self.role is not None:
            object.__setattr__(self, "role", _clean(self.role).strip() or None)
        if self.memory_scope is not None:
            object.__setattr__(
                self,
                "memory_scope",
                _clean(self.memory_scope).strip() or None,
            )
        object.__setattr__(
            self,
            "source_types",
            tuple(sorted(set(_clean(item).strip() for item in self.source_types if item))),
        )
        object.__setattr__(
            self,
            "memory_kinds",
            tuple(sorted(set(_clean(item).strip() for item in self.memory_kinds if item))),
        )
        if not 1 <= int(self.limit) <= 50:
            raise ValueError("limit must be between 1 and 50")
        if not 256 <= int(self.max_chars) <= 50000:
            raise ValueError("max_chars must be between 256 and 50000")
        object.__setattr__(self, "limit", int(self.limit))
        object.__setattr__(self, "max_chars", int(self.max_chars))


@dataclass(frozen=True)
class RetrievalDocument:
    document_id: str
    source_type: str
    text: str
    refs: tuple[str, ...]
    source_version: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    sensitivity: str = "internal"
    visibility: tuple[str, ...] = ()
    head_bound: bool = False

    def __post_init__(self) -> None:
        for name in ("document_id", "source_type", "text", "source_version"):
            value = _clean(getattr(self, name)).strip()
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value)
        refs = tuple(_clean(item).strip() for item in self.refs if _clean(item).strip())
        if not refs:
            raise ValueError("refs are required")
        object.__setattr__(self, "refs", refs)
        if self.sensitivity not in {"public", "internal", "sensitive"}:
            raise ValueError("unsupported retrieval sensitivity")
        object.__setattr__(
            self,
            "visibility",
            tuple(sorted(set(_clean(item).strip() for item in self.visibility if item))),
        )
        clean_metadata = sanitize_trace_value(dict(self.metadata))
        object.__setattr__(self, "metadata", _freeze(clean_metadata))

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "source_type": self.source_type,
            "text": self.text,
            "refs": list(self.refs),
            "source_version": self.source_version,
            "metadata": _thaw(self.metadata),
            "sensitivity": self.sensitivity,
            "visibility": list(self.visibility),
            "head_bound": self.head_bound,
        }


@dataclass(frozen=True)
class RetrievalHit:
    document: RetrievalDocument
    score: float
    matched_terms: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "document": self.document.as_dict(),
            "score": round(float(self.score), 6),
            "matched_terms": list(self.matched_terms),
        }


@dataclass(frozen=True)
class RetrievalResult:
    status: str
    hits: tuple[RetrievalHit, ...]
    documents_considered: int
    stale_filtered: int = 0
    visibility_filtered: int = 0
    sensitive_filtered: int = 0
    source_reads_avoided: int = 0
    chars_returned: int = 0
    truncated: bool = False

    def __post_init__(self) -> None:
        if self.status not in {"hit", "miss"}:
            raise ValueError("retrieval status must be hit or miss")
        for name in (
            "documents_considered",
            "stale_filtered",
            "visibility_filtered",
            "sensitive_filtered",
            "source_reads_avoided",
            "chars_returned",
        ):
            if int(getattr(self, name)) < 0:
                raise ValueError(f"{name} must be non-negative")

    def as_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for hit in self.hits:
            source_type = hit.document.source_type
            counts[source_type] = counts.get(source_type, 0) + 1
        return {
            "status": self.status,
            "hits": [hit.as_dict() for hit in self.hits],
            "documents_considered": self.documents_considered,
            "stale_filtered": self.stale_filtered,
            "visibility_filtered": self.visibility_filtered,
            "sensitive_filtered": self.sensitive_filtered,
            "source_reads_avoided": self.source_reads_avoided,
            "chars_returned": self.chars_returned,
            "truncated": self.truncated,
            "source_counts": counts,
        }
