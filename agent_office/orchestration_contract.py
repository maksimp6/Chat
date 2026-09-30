"""Task orchestration contract for agent_office."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Optional

from agent_context import EvidenceRef
from agent_office.dispatch_model import CANONICAL_LIFECYCLE_STAGES, _ALL_BACKENDS

# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class DuplicateActiveTaskError(Exception):
    pass


class MissingEvidenceError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Advisory opinion
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BriefOpinion:
    likes: Optional[str] = None
    concerns: Optional[str] = None
    improvement: Optional[str] = None


# --------------------------------------------------------------------------- #
# Lifecycle table
# --------------------------------------------------------------------------- #

_LIFECYCLE_TABLE: dict[tuple[str, Optional[str]], Optional[str]] = {
    ("contract", "RED_READY"): "contract-review",
    ("contract-review", "ACCEPTED"): "implementation",
    ("implementation", "success"): "verification",
    ("verification", "success"): "solution-review",
    ("solution-review", "ACCEPTED"): "maintain",
    ("solution-review", "CHANGES_REQUESTED"): "implementation",
    ("maintain", "READY"): None,
}

_IMPLEMENTATION_ONWARD = frozenset(
    {"implementation", "verification", "solution-review", "maintain"}
)

_TERMINAL_STATES = frozenset({"done", "failed", "stalled"})
_FAILED_STALLED_STATES = frozenset({"failed", "stalled"})


def derive_active_task_key(work_item_ref: str) -> str:
    """Deterministic work-item-scoped key; no stage component."""
    digest = sha256(work_item_ref.encode("utf-8")).hexdigest()[:16]
    return f"task:{digest}"


def next_stage_after(stage: str, outcome: Optional[str]) -> Optional[str]:
    """Pure lifecycle table lookup; returns None for blocking/unknown outcomes."""
    return _LIFECYCLE_TABLE.get((stage, outcome), None)


# --------------------------------------------------------------------------- #
# Packet
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class OrchestratedTaskPacket:
    task_id: str
    work_item_ref: str
    stage: str
    backend: str
    base_ref: str
    evidence_refs: tuple[EvidenceRef, ...] = field(default_factory=tuple)
    accepted_contract_head: Optional[str] = None
    brief_opinion: Optional[BriefOpinion] = None
    role: Optional[str] = None

    def __post_init__(self) -> None:
        if self.stage not in CANONICAL_LIFECYCLE_STAGES:
            raise ValueError(f"stage {self.stage!r} is not a canonical lifecycle stage")
        if self.backend not in _ALL_BACKENDS:
            raise ValueError(f"backend {self.backend!r} is not a known backend")
        if self.stage in _IMPLEMENTATION_ONWARD:
            if not self.accepted_contract_head:
                raise ValueError(
                    f"accepted_contract_head is required from implementation onward"
                    f" (stage={self.stage!r})"
                )
            required = EvidenceRef(
                kind="contract",
                ref=self.accepted_contract_head,
                version="accepted",
            )
            if required not in self.evidence_refs:
                raise ValueError(
                    "evidence_refs must contain a contract EvidenceRef from implementation onward"
                )

    @property
    def active_task_key(self) -> str:
        return derive_active_task_key(self.work_item_ref)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


@dataclass
class _TaskRecord:
    packet: OrchestratedTaskPacket
    state: str
    evidence_refs: tuple[EvidenceRef, ...] = field(default_factory=tuple)
    material_outcome: Optional[str] = None

    @property
    def is_terminal(self) -> bool:
        return self.state in _TERMINAL_STATES


class TaskRegistry:
    def __init__(self) -> None:
        self._records: dict[str, _TaskRecord] = {}

    def register(self, packet: OrchestratedTaskPacket) -> None:
        key = packet.active_task_key
        prior = self._records.get(key)

        if prior is None:
            self._records[key] = _TaskRecord(packet=packet, state="active")
            return

        if prior.packet.task_id == packet.task_id:
            return  # idempotent continuation

        if not prior.is_terminal:
            raise DuplicateActiveTaskError(
                f"work item {packet.work_item_ref!r} already has active task"
                f" {prior.packet.task_id!r}"
            )

        if prior.state in _FAILED_STALLED_STATES:
            if packet.stage == prior.packet.stage:
                prior_evidence = set(prior.evidence_refs)
                new_evidence = set(packet.evidence_refs)
                if not (new_evidence - prior_evidence):
                    raise MissingEvidenceError(
                        "retry after failed/stalled requires at least one new EvidenceRef"
                    )
        else:
            allowed = next_stage_after(prior.packet.stage, prior.material_outcome)
            if packet.stage != allowed:
                raise DuplicateActiveTaskError(
                    f"work item {packet.work_item_ref!r}: prior stage"
                    f" {prior.packet.stage!r} with outcome {prior.material_outcome!r}"
                    f" allows {allowed!r} next but got {packet.stage!r}"
                )

        self._records[key] = _TaskRecord(packet=packet, state="active")

    def record_terminal(
        self,
        task_id: str,
        state: str,
        evidence_refs: tuple[EvidenceRef, ...] = (),
        material_outcome: Optional[str] = None,
    ) -> None:
        for record in self._records.values():
            if record.packet.task_id == task_id:
                record.state = state
                record.evidence_refs = tuple(evidence_refs)
                record.material_outcome = material_outcome
                return
        raise KeyError(f"no task with id {task_id!r}")

    def active_task_id_for(self, work_item_ref: str) -> Optional[str]:
        key = derive_active_task_key(work_item_ref)
        record = self._records.get(key)
        if record is None or record.is_terminal:
            return None
        return record.packet.task_id
