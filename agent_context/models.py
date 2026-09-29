"""Canonical, GitHub-first task packet contracts for agent context."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Mapping

from trace_security import sanitize_trace_value


_EVIDENCE_COMPONENTS = ("task", "ci", "review", "trace", "files", "skills", "policy")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(sanitize_trace_value(str(value)))


def _clean_text_tuple(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(_clean_text(value) for value in values)


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value


def _thaw_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    return value


@dataclass(frozen=True)
class TaskScope:
    """GitHub provenance and receiving role for one reusable context packet."""

    repository: str
    work_item: str
    base_sha: str
    head_sha: str
    role: str
    selected_skills: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("repository", "work_item", "base_sha", "head_sha", "role"):
            value = _clean_text(getattr(self, name)).strip()
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value)
        skills = tuple(sorted(set(_clean_text_tuple(self.selected_skills))))
        object.__setattr__(self, "selected_skills", skills)

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "work_item": self.work_item,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "role": self.role,
            "selected_skills": list(self.selected_skills),
        }

    def scope_key(self) -> str:
        return _canonical_hash(self.as_dict())


@dataclass(frozen=True)
class EvidenceVersion:
    """Versions of evidence that may invalidate only part of a task packet."""

    task: str = ""
    ci: str = ""
    review: str = ""
    trace: str = ""
    files: str = ""
    skills: str = ""
    policy: str = ""

    def __post_init__(self) -> None:
        for name in _EVIDENCE_COMPONENTS:
            object.__setattr__(self, name, _clean_text(getattr(self, name)))
        if not self.task.strip():
            raise ValueError("task evidence version is required")

    def as_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in _EVIDENCE_COMPONENTS}

    def fingerprint(self) -> str:
        return _canonical_hash(self.as_dict())

    def changed_components(self, other: "EvidenceVersion") -> tuple[str, ...]:
        return tuple(
            name for name in _EVIDENCE_COMPONENTS if getattr(self, name) != getattr(other, name)
        )


@dataclass(frozen=True)
class EvidenceRef:
    """Compact pointer to authoritative evidence, normally in GitHub or trace storage."""

    kind: str
    ref: str
    version: str = ""

    def __post_init__(self) -> None:
        if not _clean_text(self.kind).strip() or not _clean_text(self.ref).strip():
            raise ValueError("evidence kind and ref are required")
        object.__setattr__(self, "kind", _clean_text(self.kind).strip())
        object.__setattr__(self, "ref", _clean_text(self.ref).strip())
        object.__setattr__(self, "version", _clean_text(self.version))

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "ref": self.ref, "version": self.version}


@dataclass(frozen=True)
class ContextSlice:
    """A bounded reusable part of a packet with explicit evidence dependencies."""

    name: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    source_bytes: int = 0
    input_tokens: int = 0

    def __post_init__(self) -> None:
        name = _clean_text(self.name).strip()
        if not name:
            raise ValueError("slice name is required")
        unknown = sorted(set(self.depends_on) - set(_EVIDENCE_COMPONENTS))
        if unknown:
            raise ValueError(f"unsupported evidence dependencies: {', '.join(unknown)}")
        if self.source_bytes < 0 or self.input_tokens < 0:
            raise ValueError("slice usage estimates must be non-negative")
        clean_payload = sanitize_trace_value(dict(self.payload))
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "payload", _freeze_value(dict(clean_payload)))
        object.__setattr__(
            self,
            "depends_on",
            tuple(sorted(set(_clean_text_tuple(self.depends_on)))),
        )

    def reusable_when(self, changed_components: tuple[str, ...]) -> bool:
        return not set(self.depends_on).intersection(changed_components)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "payload": _thaw_value(self.payload),
            "depends_on": list(self.depends_on),
            "source_bytes": self.source_bytes,
            "input_tokens": self.input_tokens,
        }


@dataclass(frozen=True)
class TaskPacket:
    """Small, source-linked handoff packet prepared before an agent/model call."""

    scope: TaskScope
    evidence: EvidenceVersion
    objective: str
    expected_deliverable: str
    known_facts: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    failed_attempts: tuple[str, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()
    slices: tuple[ContextSlice, ...] = ()
    changed_files: tuple[str, ...] = ()
    owner: str | None = None
    budget_tier: str = "normal"
    usage: Mapping[str, Any] = field(default_factory=dict)
    escalation_target: str | None = None

    def __post_init__(self) -> None:
        objective = _clean_text(self.objective).strip()
        deliverable = _clean_text(self.expected_deliverable).strip()
        if not objective or not deliverable:
            raise ValueError("objective and expected_deliverable are required")
        object.__setattr__(self, "objective", objective)
        object.__setattr__(self, "expected_deliverable", deliverable)
        object.__setattr__(self, "known_facts", _clean_text_tuple(self.known_facts))
        object.__setattr__(self, "open_questions", _clean_text_tuple(self.open_questions))
        object.__setattr__(self, "failed_attempts", _clean_text_tuple(self.failed_attempts))
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(self, "slices", tuple(self.slices))
        object.__setattr__(self, "changed_files", _clean_text_tuple(self.changed_files))
        if self.owner is not None:
            object.__setattr__(self, "owner", _clean_text(self.owner))
        object.__setattr__(
            self,
            "budget_tier",
            _clean_text(self.budget_tier).strip() or "normal",
        )
        clean_usage = sanitize_trace_value(dict(self.usage))
        object.__setattr__(self, "usage", _freeze_value(dict(clean_usage)))
        if self.escalation_target is not None:
            object.__setattr__(
                self,
                "escalation_target",
                _clean_text(self.escalation_target),
            )

    def cache_key(self) -> str:
        return _canonical_hash(
            {
                "scope": self.scope.as_dict(),
                "evidence": self.evidence.as_dict(),
            }
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope.as_dict(),
            "evidence": self.evidence.as_dict(),
            "objective": self.objective,
            "expected_deliverable": self.expected_deliverable,
            "known_facts": list(self.known_facts),
            "open_questions": list(self.open_questions),
            "failed_attempts": list(self.failed_attempts),
            "evidence_refs": [item.as_dict() for item in self.evidence_refs],
            "slices": [item.as_dict() for item in self.slices],
            "changed_files": list(self.changed_files),
            "owner": self.owner,
            "budget_tier": self.budget_tier,
            "usage": _thaw_value(self.usage),
            "escalation_target": self.escalation_target,
        }
