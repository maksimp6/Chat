"""Token-efficient semantic browser state and evidence contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Iterable, Mapping, Sequence


INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "checkbox",
        "combobox",
        "link",
        "menuitem",
        "option",
        "radio",
        "searchbox",
        "slider",
        "spinbutton",
        "switch",
        "tab",
        "textbox",
    }
)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _bounded_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _normalize_states(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, Mapping)):
        values = list(value)
    else:
        values = []
    return tuple(sorted({str(item).strip() for item in values if str(item).strip()}))


@dataclass(frozen=True)
class SemanticNode:
    node_id: str
    role: str
    name: str | None = None
    value: str | None = None
    states: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, index: int) -> "SemanticNode":
        node_id = str(raw.get("id") or raw.get("node_id") or f"n{index}").strip()
        role = str(raw.get("role") or "generic").strip().lower()
        return cls(
            node_id=node_id,
            role=role,
            name=_bounded_text(raw.get("name") or raw.get("label"), 240),
            value=_bounded_text(raw.get("value") or raw.get("text"), 320),
            states=_normalize_states(raw.get("states")),
        )

    @property
    def interactive(self) -> bool:
        return self.role in INTERACTIVE_ROLES or "focusable" in self.states

    def to_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.node_id, "role": self.role}
        if self.name is not None:
            result["name"] = self.name
        if self.value is not None:
            result["value"] = self.value
        if self.states:
            result["states"] = list(self.states)
        return result


@dataclass(frozen=True)
class SemanticSnapshot:
    url: str | None
    title: str | None
    version: str | None
    nodes: tuple[SemanticNode, ...] = ()
    facts: Mapping[str, Any] = field(default_factory=dict)
    truncated: bool = False

    def to_mapping(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "version": self.version,
            "nodes": [node.to_mapping() for node in self.nodes],
            "facts": dict(self.facts),
            "truncated": self.truncated,
        }


def build_semantic_snapshot(
    raw: Mapping[str, Any],
    *,
    max_nodes: int = 80,
    max_chars: int = 8000,
    interactive_only: bool = False,
) -> SemanticSnapshot:
    if not isinstance(raw, Mapping):
        raise TypeError("semantic snapshot input must be a mapping")
    if max_nodes < 1:
        raise ValueError("max_nodes must be >= 1")
    if max_chars < 128:
        raise ValueError("max_chars must be >= 128")

    raw_nodes = raw.get("nodes")
    if raw_nodes is None:
        raw_nodes = raw.get("elements")
    if raw_nodes is None:
        raw_nodes = []
    if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes, bytearray)):
        raise ValueError("nodes must be an array")

    nodes: list[SemanticNode] = []
    truncated = False
    for index, item in enumerate(raw_nodes):
        if not isinstance(item, Mapping):
            continue
        node = SemanticNode.from_mapping(item, index=index)
        if interactive_only and not node.interactive:
            continue
        if len(nodes) >= max_nodes:
            truncated = True
            break
        nodes.append(node)

    raw_facts = raw.get("facts") or {}
    if not isinstance(raw_facts, Mapping):
        raise ValueError("facts must be an object")
    facts = {str(key): value for key, value in raw_facts.items()}

    snapshot = SemanticSnapshot(
        url=_bounded_text(raw.get("url"), 1024),
        title=_bounded_text(raw.get("title"), 320),
        version=_bounded_text(raw.get("version"), 128),
        nodes=tuple(nodes),
        facts=facts,
        truncated=truncated,
    )

    while nodes and len(_stable_json(snapshot.to_mapping())) > max_chars:
        nodes.pop()
        truncated = True
        snapshot = SemanticSnapshot(
            url=snapshot.url,
            title=snapshot.title,
            version=snapshot.version,
            nodes=tuple(nodes),
            facts=facts,
            truncated=True,
        )

    if len(_stable_json(snapshot.to_mapping())) > max_chars and facts:
        compact_facts: dict[str, Any] = {}
        for key in sorted(facts):
            candidate = {**compact_facts, key: facts[key]}
            candidate_snapshot = SemanticSnapshot(
                url=snapshot.url,
                title=snapshot.title,
                version=snapshot.version,
                nodes=snapshot.nodes,
                facts=candidate,
                truncated=True,
            )
            if len(_stable_json(candidate_snapshot.to_mapping())) > max_chars:
                break
            compact_facts = candidate
        snapshot = SemanticSnapshot(
            url=snapshot.url,
            title=snapshot.title,
            version=snapshot.version,
            nodes=snapshot.nodes,
            facts=compact_facts,
            truncated=True,
        )

    return snapshot


@dataclass(frozen=True)
class SemanticDiff:
    from_version: str | None
    to_version: str | None
    added_nodes: tuple[SemanticNode, ...] = ()
    removed_node_ids: tuple[str, ...] = ()
    changed_nodes: tuple[SemanticNode, ...] = ()
    added_facts: Mapping[str, Any] = field(default_factory=dict)
    removed_facts: tuple[str, ...] = ()
    changed_facts: Mapping[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "from_version": self.from_version,
            "to_version": self.to_version,
            "added_nodes": [node.to_mapping() for node in self.added_nodes],
            "removed_node_ids": list(self.removed_node_ids),
            "changed_nodes": [node.to_mapping() for node in self.changed_nodes],
            "added_facts": dict(self.added_facts),
            "removed_facts": list(self.removed_facts),
            "changed_facts": dict(self.changed_facts),
        }


def diff_semantic_snapshots(before: SemanticSnapshot, after: SemanticSnapshot) -> SemanticDiff:
    if not isinstance(before, SemanticSnapshot) or not isinstance(after, SemanticSnapshot):
        raise TypeError("semantic diff requires SemanticSnapshot values")

    before_nodes = {node.node_id: node for node in before.nodes}
    after_nodes = {node.node_id: node for node in after.nodes}

    added_ids = sorted(set(after_nodes) - set(before_nodes))
    removed_ids = tuple(sorted(set(before_nodes) - set(after_nodes)))
    changed_ids = sorted(
        node_id
        for node_id in set(before_nodes) & set(after_nodes)
        if before_nodes[node_id] != after_nodes[node_id]
    )

    added_facts = {key: after.facts[key] for key in sorted(set(after.facts) - set(before.facts))}
    removed_facts = tuple(sorted(set(before.facts) - set(after.facts)))
    changed_facts = {
        key: {"before": before.facts[key], "after": after.facts[key]}
        for key in sorted(set(before.facts) & set(after.facts))
        if _stable_json(before.facts[key]) != _stable_json(after.facts[key])
    }

    return SemanticDiff(
        from_version=before.version,
        to_version=after.version,
        added_nodes=tuple(after_nodes[node_id] for node_id in added_ids),
        removed_node_ids=removed_ids,
        changed_nodes=tuple(after_nodes[node_id] for node_id in changed_ids),
        added_facts=added_facts,
        removed_facts=removed_facts,
        changed_facts=changed_facts,
    )


@dataclass(frozen=True)
class Evidence:
    field: str
    value: Any
    source: str
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.field.strip():
            raise ValueError("evidence field is required")
        if not self.source.strip():
            raise ValueError("evidence source is required")
        if not 0 <= self.confidence <= 1:
            raise ValueError("evidence confidence must be between 0 and 1")


@dataclass(frozen=True)
class MergedEvidence:
    facts: Mapping[str, Any]
    conflicts: Mapping[str, tuple[dict[str, Any], ...]]
    sources: Mapping[str, tuple[str, ...]]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "facts": dict(self.facts),
            "conflicts": {
                field: [dict(item) for item in values] for field, values in self.conflicts.items()
            },
            "sources": {field: list(values) for field, values in self.sources.items()},
        }


def merge_evidence(
    evidence: Sequence[Evidence],
    *,
    max_fields: int = 64,
    max_conflicts_per_field: int = 8,
) -> MergedEvidence:
    if max_fields < 1:
        raise ValueError("max_fields must be >= 1")
    if max_conflicts_per_field < 1:
        raise ValueError("max_conflicts_per_field must be >= 1")

    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for item in evidence:
        field = item.field.strip()
        key = _stable_json(item.value)
        bucket = grouped.setdefault(field, {})
        candidate = bucket.setdefault(
            key,
            {
                "value": item.value,
                "confidence": 0.0,
                "sources": set(),
            },
        )
        candidate["confidence"] = max(float(candidate["confidence"]), item.confidence)
        candidate["sources"].add(item.source.strip())

    facts: dict[str, Any] = {}
    conflicts: dict[str, tuple[dict[str, Any], ...]] = {}
    sources: dict[str, tuple[str, ...]] = {}

    for field in sorted(grouped)[:max_fields]:
        candidates = list(grouped[field].values())
        candidates.sort(
            key=lambda item: (
                -float(item["confidence"]),
                _stable_json(item["value"]),
            )
        )
        if len(candidates) == 1:
            winner = candidates[0]
            facts[field] = winner["value"]
            sources[field] = tuple(sorted(winner["sources"]))
            continue

        conflicts[field] = tuple(
            {
                "value": candidate["value"],
                "confidence": candidate["confidence"],
                "sources": sorted(candidate["sources"]),
            }
            for candidate in candidates[:max_conflicts_per_field]
        )

    return MergedEvidence(facts=facts, conflicts=conflicts, sources=sources)


__all__ = [
    "Evidence",
    "INTERACTIVE_ROLES",
    "MergedEvidence",
    "SemanticDiff",
    "SemanticNode",
    "SemanticSnapshot",
    "build_semantic_snapshot",
    "diff_semantic_snapshots",
    "merge_evidence",
]
