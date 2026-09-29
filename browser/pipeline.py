"""Semantic synthesis and cheap-first escalation for BrowserRoleDag results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .orchestration import BrowserDagResult, BrowserRoleDag, BrowserRoleTask
from .semantic import (
    Evidence,
    MergedEvidence,
    SemanticDiff,
    SemanticNode,
    SemanticSnapshot,
    build_semantic_snapshot,
    diff_semantic_snapshots,
    merge_evidence,
)


SemanticRawWorker = Callable[[BrowserRoleTask], Any]


def _raw_data(raw_result: Any) -> Any:
    if isinstance(raw_result, Mapping) and "success" in raw_result:
        if raw_result.get("success") is False:
            return raw_result
        if "data" in raw_result:
            return raw_result.get("data")
    return raw_result


def _copy_usage(raw_result: Any, result: dict[str, Any]) -> None:
    if not isinstance(raw_result, Mapping):
        return
    metadata = raw_result.get("metadata")
    if isinstance(metadata, Mapping):
        result["metadata"] = dict(metadata)
    usage = raw_result.get("usage")
    if isinstance(usage, Mapping):
        result["usage"] = dict(usage)


class SemanticBrowserWorker:
    """Convert selected raw browser worker results into semantic snapshots."""

    def __init__(self, worker: SemanticRawWorker) -> None:
        if not callable(worker):
            raise TypeError("worker must be callable")
        self.worker = worker

    def __call__(self, task: BrowserRoleTask) -> Any:
        raw_result = self.worker(task)
        if isinstance(raw_result, Mapping) and raw_result.get("success") is False:
            return raw_result

        mode = str(task.metadata.get("semantic_mode") or "").strip().lower()
        if not mode:
            mode = (
                "snapshot" if task.role in {"observer", "extractor", "verifier"} else "passthrough"
            )
        if mode == "passthrough":
            return raw_result
        if mode != "snapshot":
            raise ValueError(f"unsupported semantic_mode: {mode}")

        data = _raw_data(raw_result)
        if not isinstance(data, Mapping):
            raise ValueError("semantic snapshot worker result must be a mapping")

        max_nodes = int(task.metadata.get("max_nodes") or 80)
        max_chars = int(task.metadata.get("max_chars") or task.budget.max_output_chars)
        interactive_only = bool(task.metadata.get("interactive_only", False))
        snapshot = build_semantic_snapshot(
            data,
            max_nodes=max_nodes,
            max_chars=max_chars,
            interactive_only=interactive_only,
        )
        result = {
            "success": True,
            "data": {
                "semantic_type": "snapshot",
                "snapshot": snapshot.to_mapping(),
            },
        }
        _copy_usage(raw_result, result)
        return result


def _snapshot_from_mapping(raw: Mapping[str, Any]) -> SemanticSnapshot:
    raw_nodes = raw.get("nodes") or []
    nodes = tuple(
        SemanticNode.from_mapping(item, index=index)
        for index, item in enumerate(raw_nodes)
        if isinstance(item, Mapping)
    )
    facts = raw.get("facts") or {}
    if not isinstance(facts, Mapping):
        facts = {}
    return SemanticSnapshot(
        url=str(raw.get("url")).strip() if raw.get("url") is not None else None,
        title=str(raw.get("title")).strip() if raw.get("title") is not None else None,
        version=str(raw.get("version")).strip() if raw.get("version") is not None else None,
        nodes=nodes,
        facts={str(key): value for key, value in facts.items()},
        truncated=bool(raw.get("truncated", False)),
    )


@dataclass(frozen=True)
class BrowserSynthesisPolicy:
    max_snapshots: int = 32
    max_diffs: int = 32
    max_evidence_fields: int = 64
    max_conflicts_per_field: int = 8
    escalate_on_truncation: bool = True

    def __post_init__(self) -> None:
        if self.max_snapshots < 1:
            raise ValueError("max_snapshots must be >= 1")
        if self.max_diffs < 1:
            raise ValueError("max_diffs must be >= 1")
        if self.max_evidence_fields < 1:
            raise ValueError("max_evidence_fields must be >= 1")
        if self.max_conflicts_per_field < 1:
            raise ValueError("max_conflicts_per_field must be >= 1")


@dataclass(frozen=True)
class EscalationDecision:
    required: bool
    suggested_tier: str
    reasons: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "suggested_tier": self.suggested_tier,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class BrowserSynthesisResult:
    status: str
    task_statuses: Mapping[str, str]
    snapshots: Mapping[str, SemanticSnapshot]
    diffs: Mapping[str, SemanticDiff]
    evidence: MergedEvidence
    consumed_tokens: int
    escalation: EscalationDecision

    def to_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "task_statuses": dict(self.task_statuses),
            "snapshots": {
                task_id: snapshot.to_mapping() for task_id, snapshot in self.snapshots.items()
            },
            "diffs": {task_id: diff.to_mapping() for task_id, diff in self.diffs.items()},
            "evidence": self.evidence.to_mapping(),
            "consumed_tokens": self.consumed_tokens,
            "escalation": self.escalation.to_mapping(),
        }


def _extract_snapshot(data: Any) -> SemanticSnapshot | None:
    if not isinstance(data, Mapping):
        return None
    if data.get("semantic_type") != "snapshot":
        return None
    raw_snapshot = data.get("snapshot")
    if not isinstance(raw_snapshot, Mapping):
        return None
    return _snapshot_from_mapping(raw_snapshot)


def _escalation(
    dag_result: BrowserDagResult,
    evidence: MergedEvidence,
    snapshots: Mapping[str, SemanticSnapshot],
    missing_diff_sources: list[str],
    policy: BrowserSynthesisPolicy,
) -> EscalationDecision:
    reasons: list[str] = []
    strong = False

    non_success = sorted(
        task_id for task_id, result in dag_result.tasks.items() if result.status != "succeeded"
    )
    if non_success:
        reasons.append("task_failures:" + ",".join(non_success))
        strong = True

    if evidence.conflicts:
        reasons.append("evidence_conflicts:" + ",".join(sorted(evidence.conflicts)))
        strong = True

    if missing_diff_sources:
        reasons.append("missing_diff_sources:" + ",".join(sorted(missing_diff_sources)))
        strong = True

    truncated = sorted(task_id for task_id, snapshot in snapshots.items() if snapshot.truncated)
    if truncated and policy.escalate_on_truncation:
        reasons.append("truncated_state:" + ",".join(truncated))

    if strong:
        tier = "strong"
    elif reasons:
        tier = "cheap"
    else:
        tier = "deterministic"

    return EscalationDecision(bool(reasons), tier, tuple(reasons))


def synthesize_browser_dag(
    dag: BrowserRoleDag,
    dag_result: BrowserDagResult,
    *,
    policy: BrowserSynthesisPolicy | None = None,
) -> BrowserSynthesisResult:
    """Produce a compact semantic result from completed BrowserRoleDag work."""
    if not isinstance(dag, BrowserRoleDag):
        raise TypeError("dag must be a BrowserRoleDag")
    if not isinstance(dag_result, BrowserDagResult):
        raise TypeError("dag_result must be a BrowserDagResult")

    active_policy = policy or BrowserSynthesisPolicy()
    task_map = {task.task_id: task for task in dag.tasks}

    snapshots: dict[str, SemanticSnapshot] = {}
    for task_id in sorted(dag_result.tasks):
        result = dag_result.tasks[task_id]
        if result.status != "succeeded":
            continue
        snapshot = _extract_snapshot(result.data)
        if snapshot is None:
            continue
        if len(snapshots) >= active_policy.max_snapshots:
            break
        snapshots[task_id] = snapshot

    evidence_items: list[Evidence] = []
    for task_id, snapshot in snapshots.items():
        task = task_map.get(task_id)
        if task is None:
            continue
        source = str(task.metadata.get("source") or task_id)
        try:
            confidence = float(task.metadata.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        confidence = min(1.0, max(0.0, confidence))
        for field_name, value in snapshot.facts.items():
            evidence_items.append(Evidence(field_name, value, source, confidence))

    merged = merge_evidence(
        evidence_items,
        max_fields=active_policy.max_evidence_fields,
        max_conflicts_per_field=active_policy.max_conflicts_per_field,
    )

    diffs: dict[str, SemanticDiff] = {}
    missing_diff_sources: list[str] = []
    for task_id, snapshot in snapshots.items():
        if len(diffs) >= active_policy.max_diffs:
            break
        task = task_map.get(task_id)
        if task is None:
            continue
        diff_from = str(task.metadata.get("diff_from") or "").strip()
        if not diff_from:
            continue
        previous = snapshots.get(diff_from)
        if previous is None:
            missing_diff_sources.append(f"{task_id}<-{diff_from}")
            continue
        diffs[task_id] = diff_semantic_snapshots(previous, snapshot)

    escalation = _escalation(
        dag_result,
        merged,
        snapshots,
        missing_diff_sources,
        active_policy,
    )
    task_statuses = {task_id: result.status for task_id, result in sorted(dag_result.tasks.items())}
    return BrowserSynthesisResult(
        status=dag_result.status,
        task_statuses=task_statuses,
        snapshots=snapshots,
        diffs=diffs,
        evidence=merged,
        consumed_tokens=dag_result.consumed_tokens,
        escalation=escalation,
    )


__all__ = [
    "BrowserSynthesisPolicy",
    "BrowserSynthesisResult",
    "EscalationDecision",
    "SemanticBrowserWorker",
    "synthesize_browser_dag",
]
