"""Provider-neutral task state machine for agent-office dispatched tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple


_KNOWN_BACKEND_STATUSES: frozenset[str] = frozenset({"completed", "failed"})


class AgentTaskStateError(Exception):
    pass


@dataclass
class AgentTaskEvidence:
    dispatch_created: bool = False
    mention_delivered: bool = False
    subscribed: bool = False
    workflow_status: Optional[str] = None
    workflow_conclusion: Optional[str] = None
    backend_triggered: bool = False
    backend_status: Optional[str] = None
    deliverable_refs: Tuple[str, ...] = field(default_factory=tuple)
    validation_status: Optional[str] = None
    review_complete: bool = False
    blocker: Optional[str] = None
    cancelled: bool = False
    stall_detected: bool = False


def derive_agent_task_state(evidence: AgentTaskEvidence) -> str:
    # Fail-closed input validation
    if (
        evidence.backend_status is not None
        and evidence.backend_status not in _KNOWN_BACKEND_STATUSES
    ):
        raise AgentTaskStateError(
            f"unsupported backend status: {evidence.backend_status!r}"
        )
    if evidence.review_complete and evidence.validation_status != "success":
        raise AgentTaskStateError("review cannot complete before validation")

    # Terminal precedence: cancelled > blocked > failed > stalled
    if evidence.cancelled:
        return "cancelled"
    if evidence.blocker:
        return "blocked"
    if evidence.backend_status == "failed":
        return "failed"
    if evidence.stall_detected:
        return "stalled"

    # Success without a required deliverable fails closed
    if evidence.backend_status == "completed" and not evidence.deliverable_refs:
        return "failed"

    # Non-terminal progress states (highest progress first)
    if (
        evidence.deliverable_refs
        and evidence.validation_status == "success"
        and evidence.review_complete
        and evidence.backend_status == "completed"
    ):
        return "done"
    if (
        evidence.deliverable_refs
        and evidence.validation_status == "success"
        and not evidence.review_complete
    ):
        return "review"
    if evidence.deliverable_refs and evidence.validation_status == "in_progress":
        return "verifying"
    if evidence.workflow_status == "in_progress" and evidence.backend_triggered:
        return "working"
    if evidence.workflow_status in ("queued", "in_progress"):
        return "dispatched"
    if evidence.mention_delivered or evidence.subscribed:
        return "acknowledged"

    return "pending"
