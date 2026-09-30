"""Contracts for bounded agent retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from trace_security import sanitize_trace_value


MAX_RESULTS = 12
MAX_CHARS = 12000


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(sanitize_trace_value(str(value))).strip()


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
    work_item: str
    branch: str
    head_sha: str
    role: str
    skills_version: str
    selected_skills: tuple[str, ...] = ()
    max_results: int = 8
    max_chars: int = 6000

    def __post_init__(self) -> None:
        for name in (
            "text",
            "repository",
            "work_item",
            "branch",
            "head_sha",
            "role",
            "skills_version",
        ):
            value = _clean(getattr(self, name))
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value)
        object.__setattr__(
            self,
            "selected_skills",
            tuple(sorted(set(_clean(item) for item in self.selected_skills if _clean(item)))),
        )
        if not 1 <= int(self.max_results) <= MAX_RESULTS:
            raise ValueError(f"max_results must be between 1 and {MAX_RESULTS}")
        if not 128 <= int(self.max_chars) <= MAX_CHARS:
            raise ValueError(f"max_chars must be between 128 and {MAX_CHARS}")
        object.__setattr__(self, "max_results", int(self.max_results))
        object.__setattr__(self, "max_chars", int(self.max_chars))


@dataclass(frozen=True)
class RetrievalHit:
    source_type: str
    ref: str
    score: float
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("source_type", "ref", "text"):
            value = _clean(getattr(self, name))
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "score", float(self.score))
        object.__setattr__(
            self,
            "metadata",
            _freeze(sanitize_trace_value(dict(self.metadata))),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "ref": self.ref,
            "score": self.score,
            "text": self.text,
            "metadata": _thaw(self.metadata),
        }


@dataclass(frozen=True)
class RetrievalBundle:
    status: str
    hits: tuple[RetrievalHit, ...] = ()
    cache_status: str = "miss"
    source_counts: Mapping[str, int] = field(default_factory=dict)
    total_chars: int = 0
    saved_source_bytes: int = 0
    saved_input_tokens: int = 0

    def __post_init__(self) -> None:
        if self.status not in {"hit", "miss"}:
            raise ValueError("retrieval status must be hit or miss")
        if self.cache_status not in {"hit", "partial", "miss"}:
            raise ValueError("unsupported cache status")
        object.__setattr__(self, "hits", tuple(self.hits))
        object.__setattr__(
            self,
            "source_counts",
            _freeze({str(key): int(value) for key, value in self.source_counts.items()}),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "hits": [hit.as_dict() for hit in self.hits],
            "cache_status": self.cache_status,
            "source_counts": _thaw(self.source_counts),
            "total_chars": self.total_chars,
            "saved_source_bytes": self.saved_source_bytes,
            "saved_input_tokens": self.saved_input_tokens,
        }
