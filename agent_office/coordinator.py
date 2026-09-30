"""Shared task preparation and recipient-specific handoffs for agent work."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from types import MappingProxyType
from typing import Any, Mapping

from agent_context import CacheLookup, TaskPacket
from agent_retrieval import HybridRetriever, RetrievalBundle, RetrievalQuery, record_retrieval
from trace_manager import ExecutionTrace
from trace_security import sanitize_trace_value

from .dispatch_model import AgentTaskPlan
from .task_state import AgentTaskEvidence, derive_agent_task_state


AUDIENCES = frozenset({"specialist", "maintainer", "observer", "governor", "user"})
REASONING_TIERS = frozenset({"cheap", "normal", "strong"})
_BUDGET_RANK = {"cheap": 0, "normal": 1, "strong": 2}
_URGENCY = frozenset({"low", "normal", "high", "urgent"})


class CoordinatorError(ValueError):
    pass


class CoordinatorScopeError(CoordinatorError):
    pass


class CoordinatorPolicyError(CoordinatorError):
    pass


class NoNewEvidenceError(CoordinatorPolicyError):
    pass


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(sanitize_trace_value(str(value))).strip()


def _normalize_role(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _clean(value).lower()).strip("-")


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


def _nonnegative_int(value: Any, name: str) -> int:
    try:
        result = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise CoordinatorPolicyError(f"{name} must be an integer") from exc
    if result < 0:
        raise CoordinatorPolicyError(f"{name} must be non-negative")
    return result


@dataclass(frozen=True)
class CoordinatorSoftContext:
    urgency: str = "normal"
    confidence: float | None = None
    latest_event: str | None = None
    blocker: str | None = None

    def __post_init__(self) -> None:
        urgency = _clean(self.urgency).lower() or "normal"
        if urgency not in _URGENCY:
            raise CoordinatorPolicyError(f"unsupported urgency: {urgency}")
        object.__setattr__(self, "urgency", urgency)
        if self.confidence is not None:
            confidence = float(self.confidence)
            if not 0.0 <= confidence <= 1.0:
                raise CoordinatorPolicyError("confidence must be between 0 and 1")
            object.__setattr__(self, "confidence", confidence)
        for name in ("latest_event", "blocker"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _clean(value) or None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "urgency": self.urgency,
            "confidence": self.confidence,
            "latest_event": self.latest_event,
            "blocker": self.blocker,
        }


@dataclass(frozen=True)
class CoordinatorHandoff:
    audience: str
    recipient_role: str
    owner_role: str
    stage: str
    backend: str
    task_state: str
    reasoning_tier: str
    evidence_fingerprint: str
    repository: str
    work_item: str
    head_sha: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.audience not in AUDIENCES:
            raise CoordinatorPolicyError(f"unsupported handoff audience: {self.audience}")
        for name in (
            "recipient_role",
            "owner_role",
            "stage",
            "backend",
            "task_state",
            "reasoning_tier",
            "evidence_fingerprint",
            "repository",
            "work_item",
            "head_sha",
        ):
            value = _clean(getattr(self, name))
            if not value:
                raise CoordinatorPolicyError(f"{name} is required")
            object.__setattr__(self, name, value)
        if self.reasoning_tier not in REASONING_TIERS:
            raise CoordinatorPolicyError(f"unsupported reasoning tier: {self.reasoning_tier}")
        clean_payload = sanitize_trace_value(dict(self.payload))
        object.__setattr__(self, "payload", _freeze(clean_payload))

    def as_dict(self) -> dict[str, Any]:
        return {
            "audience": self.audience,
            "recipient_role": self.recipient_role,
            "owner_role": self.owner_role,
            "stage": self.stage,
            "backend": self.backend,
            "task_state": self.task_state,
            "reasoning_tier": self.reasoning_tier,
            "evidence_fingerprint": self.evidence_fingerprint,
            "repository": self.repository,
            "work_item": self.work_item,
            "head_sha": self.head_sha,
            "payload": _thaw(self.payload),
        }


def assert_reasoning_allowed(
    packet: TaskPacket,
    *,
    requested_tier: str,
    previous_strong_evidence_fingerprint: str | None = None,
) -> str:
    tier = _clean(requested_tier).lower()
    if tier not in REASONING_TIERS:
        raise CoordinatorPolicyError(f"unsupported reasoning tier: {tier}")
    budget = _clean(packet.budget_tier).lower()
    if budget not in _BUDGET_RANK:
        raise CoordinatorPolicyError(f"unsupported budget tier: {budget}")
    if _BUDGET_RANK[tier] > _BUDGET_RANK[budget]:
        raise CoordinatorPolicyError(f"reasoning tier {tier} exceeds task budget {budget}")

    fingerprint = packet.cache_key()
    if (
        tier == "strong"
        and previous_strong_evidence_fingerprint is not None
        and _clean(previous_strong_evidence_fingerprint) == fingerprint
    ):
        raise NoNewEvidenceError("strong reasoning rejected because no new evidence is available")
    return fingerprint


def _billing_summary(trace: ExecutionTrace) -> dict[str, Any]:
    billing = trace.trace.get("billing") or {}
    keys = (
        "currency",
        "cost_status",
        "total_cost",
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "total_tokens",
        "cache_savings",
    )
    return {key: billing.get(key) for key in keys}


def _usage_summary(
    packet: TaskPacket,
    bundle: RetrievalBundle,
    trace: ExecutionTrace,
    reasoning_tier: str,
) -> dict[str, Any]:
    packet_usage = dict(packet.usage)
    calls = {
        name: _nonnegative_int(packet_usage.get(name, 0), name)
        for name in ("cheap_calls", "normal_calls", "strong_calls")
    }
    return {
        **calls,
        "budget_tier": packet.budget_tier,
        "requested_reasoning_tier": reasoning_tier,
        "cache_status": bundle.cache_status,
        "retrieval_status": bundle.status,
        "retrieval_source_counts": dict(bundle.source_counts),
        "saved_source_bytes": bundle.saved_source_bytes,
        "saved_input_tokens": bundle.saved_input_tokens,
        "billing": _billing_summary(trace),
    }


def _context_refs(bundle: RetrievalBundle) -> list[dict[str, Any]]:
    return [
        {
            "source_type": hit.source_type,
            "ref": hit.ref,
            "score": hit.score,
        }
        for hit in bundle.hits
    ]


def _guidance(audience: str) -> tuple[str, ...]:
    return {
        "specialist": (
            "Use the supplied evidence and boundaries; do not rediscover unchanged context.",
            "Produce only the expected deliverable and report new evidence.",
        ),
        "maintainer": (
            "Evaluate readiness from exact-head evidence and blockers only.",
            "Do not reuse stale checks or bypass protected gates.",
        ),
        "observer": (
            "Use counts, state transitions and evidence changes; do not infer blame.",
            "Escalate only when deterministic non-convergence thresholds are met.",
        ),
        "governor": (
            "Diagnose the process gap, not the disputed implementation itself.",
            "Prefer the smallest measurable process correction.",
        ),
        "user": (
            "Explain current state plainly and surface only meaningful progress or blockers.",
            "Do not invent progress, cost, certainty or reassurance.",
        ),
    }[audience]


def _build_payload(
    *,
    audience: str,
    packet: TaskPacket,
    branch: str,
    bundle: RetrievalBundle,
    task_state: str,
    usage: Mapping[str, Any],
    soft: CoordinatorSoftContext,
    reasoning_tier: str,
) -> dict[str, Any]:
    evidence_refs = [item.as_dict() for item in packet.evidence_refs]
    if audience == "user":
        return {
            "objective": packet.objective,
            "task_state": task_state,
            "next_meaningful_step": packet.expected_deliverable,
            "latest_event": soft.latest_event,
            "blocker": soft.blocker,
            "budget_tier": packet.budget_tier,
            "usage": dict(usage),
            "soft_context": soft.as_dict(),
            "guidance": list(_guidance(audience)),
        }

    common = {
        "repository": packet.scope.repository,
        "work_item": packet.scope.work_item,
        "branch": branch,
        "base_sha": packet.scope.base_sha,
        "head_sha": packet.scope.head_sha,
        "objective": packet.objective,
        "task_state": task_state,
        "budget_tier": packet.budget_tier,
        "reasoning_tier": reasoning_tier,
        "usage": dict(usage),
        "soft_context": soft.as_dict(),
        "guidance": list(_guidance(audience)),
    }

    if audience == "specialist":
        return {
            **common,
            "expected_deliverable": packet.expected_deliverable,
            "known_facts": list(packet.known_facts),
            "open_questions": list(packet.open_questions),
            "do_not_repeat": list(packet.failed_attempts),
            "changed_files": list(packet.changed_files),
            "evidence_refs": evidence_refs,
            "context": [hit.as_dict() for hit in bundle.hits],
        }
    if audience == "maintainer":
        return {
            **common,
            "expected_deliverable": packet.expected_deliverable,
            "changed_files": list(packet.changed_files),
            "evidence_refs": evidence_refs,
            "context_refs": _context_refs(bundle),
            "open_questions": list(packet.open_questions),
            "blocker": soft.blocker,
        }
    if audience == "observer":
        return {
            **common,
            "context_refs": _context_refs(bundle),
            "do_not_repeat": list(packet.failed_attempts),
            "blocker": soft.blocker,
        }
    return {
        **common,
        "evidence_refs": evidence_refs,
        "open_questions": list(packet.open_questions),
        "do_not_repeat": list(packet.failed_attempts),
        "escalation_target": packet.escalation_target,
        "blocker": soft.blocker,
    }


def _recipient_role(audience: str, owner_role: str) -> str:
    return {
        "specialist": owner_role,
        "maintainer": "release-manager",
        "observer": "operations-observer",
        "governor": "process-governor",
        "user": "user",
    }[audience]


def record_handoff(trace: ExecutionTrace, handoff: CoordinatorHandoff) -> dict[str, Any]:
    payload = handoff.payload
    usage = payload.get("usage") or {}
    entry = {
        "audience": handoff.audience,
        "recipient_role": handoff.recipient_role,
        "owner_role": handoff.owner_role,
        "stage": handoff.stage,
        "backend": handoff.backend,
        "task_state": handoff.task_state,
        "reasoning_tier": handoff.reasoning_tier,
        "evidence_fingerprint": handoff.evidence_fingerprint,
        "repository": handoff.repository,
        "work_item": handoff.work_item,
        "head_sha": handoff.head_sha,
        "cache_status": usage.get("cache_status"),
        "retrieval_status": usage.get("retrieval_status"),
        "saved_source_bytes": usage.get("saved_source_bytes", 0),
        "saved_input_tokens": usage.get("saved_input_tokens", 0),
    }
    trace.trace.setdefault("coordinator_handoffs", []).append(entry)
    trace.add_event(
        "coordinator_handoff_prepared",
        {
            "audience": handoff.audience,
            "recipient_role": handoff.recipient_role,
            "owner_role": handoff.owner_role,
            "task_state": handoff.task_state,
            "reasoning_tier": handoff.reasoning_tier,
            "cache_status": entry["cache_status"],
            "retrieval_status": entry["retrieval_status"],
        },
    )
    return entry


class WorkCoordinator:
    """Prepare context for an already-selected owner without taking ownership."""

    def __init__(self, retriever: HybridRetriever) -> None:
        self._retriever = retriever

    def prepare_handoff(
        self,
        *,
        plan: AgentTaskPlan,
        packet: TaskPacket,
        branch: str,
        task_evidence: AgentTaskEvidence,
        trace: ExecutionTrace,
        audience: str = "specialist",
        cache_lookup: CacheLookup | None = None,
        requested_reasoning_tier: str = "cheap",
        previous_strong_evidence_fingerprint: str | None = None,
        soft_context: CoordinatorSoftContext | None = None,
        max_results: int = 8,
        max_chars: int = 6000,
        now: int | None = None,
    ) -> CoordinatorHandoff:
        if audience not in AUDIENCES:
            raise CoordinatorPolicyError(f"unsupported handoff audience: {audience}")
        owner_role = _normalize_role(plan.role)
        packet_role = _normalize_role(packet.scope.role)
        if not owner_role:
            raise CoordinatorScopeError("coordinator requires an already-selected owner role")
        if owner_role != packet_role:
            raise CoordinatorScopeError(
                f"task packet role {packet_role!r} does not match selected owner {owner_role!r}"
            )
        branch = _clean(branch)
        if not branch:
            raise CoordinatorScopeError("branch is required")

        if packet.scope.selected_skills and not packet.evidence.skills.strip():
            raise CoordinatorScopeError(
                "selected skills require a non-empty skill evidence version"
            )
        skills_version = packet.evidence.skills.strip() or "none"
        evidence_fingerprint = assert_reasoning_allowed(
            packet,
            requested_tier=requested_reasoning_tier,
            previous_strong_evidence_fingerprint=previous_strong_evidence_fingerprint,
        )
        reasoning_tier = _clean(requested_reasoning_tier).lower()

        query_text = " ".join(
            [
                packet.objective,
                packet.expected_deliverable,
                *packet.known_facts,
                *packet.open_questions,
            ]
        )[:4000]
        query = RetrievalQuery(
            text=query_text,
            repository=packet.scope.repository,
            work_item=packet.scope.work_item,
            branch=branch,
            head_sha=packet.scope.head_sha,
            role=owner_role,
            skills_version=skills_version,
            selected_skills=packet.scope.selected_skills,
            max_results=max_results,
            max_chars=max_chars,
        )
        bundle = self._retriever.retrieve(
            query,
            cache_lookup=cache_lookup,
            now=now,
        )
        record_retrieval(trace, bundle, query)

        task_state = derive_agent_task_state(task_evidence)
        usage = _usage_summary(packet, bundle, trace, reasoning_tier)
        soft = soft_context or CoordinatorSoftContext()
        payload = _build_payload(
            audience=audience,
            packet=packet,
            branch=branch,
            bundle=bundle,
            task_state=task_state,
            usage=usage,
            soft=soft,
            reasoning_tier=reasoning_tier,
        )
        handoff = CoordinatorHandoff(
            audience=audience,
            recipient_role=_recipient_role(audience, owner_role),
            owner_role=owner_role,
            stage=plan.stage,
            backend=plan.backend,
            task_state=task_state,
            reasoning_tier=reasoning_tier,
            evidence_fingerprint=evidence_fingerprint,
            repository=packet.scope.repository,
            work_item=packet.scope.work_item,
            head_sha=packet.scope.head_sha,
            payload=payload,
        )
        record_handoff(trace, handoff)
        return handoff
