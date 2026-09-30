import pytest

from agent_office.task_state import (
    AgentTaskEvidence,
    AgentTaskStateError,
    derive_agent_task_state,
)


def test_mention_delivery_is_acknowledged_not_working():
    state = derive_agent_task_state(
        AgentTaskEvidence(
            dispatch_created=True,
            mention_delivered=True,
            subscribed=True,
        )
    )

    assert state == "acknowledged"


def test_workflow_queued_is_dispatched():
    state = derive_agent_task_state(
        AgentTaskEvidence(
            dispatch_created=True,
            workflow_status="queued",
        )
    )

    assert state == "dispatched"


def test_workflow_in_progress_without_backend_trigger_is_not_working():
    # Workflow advanced past queued, so progress is monotonic: dispatched.
    # It must never reach working without an explicit backend/session trigger.
    state = derive_agent_task_state(
        AgentTaskEvidence(
            dispatch_created=True,
            workflow_status="in_progress",
            backend_triggered=False,
        )
    )

    assert state == "dispatched"


def test_backend_trigger_proves_working():
    state = derive_agent_task_state(
        AgentTaskEvidence(
            dispatch_created=True,
            workflow_status="in_progress",
            backend_triggered=True,
        )
    )

    assert state == "working"


def test_validation_after_deliverable_is_verifying():
    state = derive_agent_task_state(
        AgentTaskEvidence(
            dispatch_created=True,
            backend_triggered=True,
            deliverable_refs=("commit:abc",),
            validation_status="in_progress",
        )
    )

    assert state == "verifying"


def test_green_deliverable_waiting_for_review_is_review():
    state = derive_agent_task_state(
        AgentTaskEvidence(
            dispatch_created=True,
            backend_triggered=True,
            deliverable_refs=("pr:42",),
            validation_status="success",
            review_complete=False,
        )
    )

    assert state == "review"


def test_done_requires_deliverable_and_completed_review():
    state = derive_agent_task_state(
        AgentTaskEvidence(
            dispatch_created=True,
            backend_triggered=True,
            backend_status="completed",
            deliverable_refs=("pr:42",),
            validation_status="success",
            review_complete=True,
        )
    )

    assert state == "done"


def test_backend_success_without_deliverable_is_failure():
    state = derive_agent_task_state(
        AgentTaskEvidence(
            dispatch_created=True,
            workflow_status="completed",
            workflow_conclusion="success",
            backend_triggered=False,
            backend_status="completed",
            deliverable_refs=(),
        )
    )

    assert state == "failed"


def test_explicit_blocker_wins_over_non_terminal_progress():
    state = derive_agent_task_state(
        AgentTaskEvidence(
            dispatch_created=True,
            backend_triggered=True,
            blocker="BLOCKED: accepted contract cannot be satisfied",
        )
    )

    assert state == "blocked"


def test_explicit_failure_wins_over_working_signal():
    state = derive_agent_task_state(
        AgentTaskEvidence(
            dispatch_created=True,
            backend_triggered=True,
            backend_status="failed",
        )
    )

    assert state == "failed"


def test_cancelled_is_terminal():
    state = derive_agent_task_state(
        AgentTaskEvidence(
            dispatch_created=True,
            cancelled=True,
            backend_triggered=True,
        )
    )

    assert state == "cancelled"


def test_stall_requires_explicit_deadline_evidence():
    state = derive_agent_task_state(
        AgentTaskEvidence(
            dispatch_created=True,
            mention_delivered=True,
            stall_detected=True,
        )
    )

    assert state == "stalled"


def test_terminal_precedence_cancelled_beats_blocked():
    # cancelled > blocked: explicit operator cancellation is the strongest terminal.
    state = derive_agent_task_state(
        AgentTaskEvidence(
            dispatch_created=True,
            cancelled=True,
            blocker="BLOCKED: dependency missing",
        )
    )

    assert state == "cancelled"


def test_terminal_precedence_blocked_beats_failed():
    # blocked > failed: an explicit material blocker is more informative than backend failure.
    state = derive_agent_task_state(
        AgentTaskEvidence(
            dispatch_created=True,
            blocker="BLOCKED: upstream review rejected",
            backend_status="failed",
        )
    )

    assert state == "blocked"


def test_terminal_precedence_failed_beats_stalled():
    # failed > stalled: concrete backend failure beats timeout/stall inference.
    state = derive_agent_task_state(
        AgentTaskEvidence(
            dispatch_created=True,
            backend_status="failed",
            stall_detected=True,
        )
    )

    assert state == "failed"


def test_terminal_precedence_all_signals_cancelled():
    # All four terminal signals present: precedence order yields cancelled.
    state = derive_agent_task_state(
        AgentTaskEvidence(
            dispatch_created=True,
            cancelled=True,
            blocker="BLOCKED: contract violated",
            backend_status="failed",
            stall_detected=True,
        )
    )

    assert state == "cancelled"


def test_unknown_backend_state_fails_closed():
    with pytest.raises(AgentTaskStateError, match="unsupported backend status"):
        derive_agent_task_state(
            AgentTaskEvidence(
                dispatch_created=True,
                backend_status="mystery",
            )
        )


def test_review_complete_without_validation_cannot_be_done():
    with pytest.raises(AgentTaskStateError, match="review cannot complete before validation"):
        derive_agent_task_state(
            AgentTaskEvidence(
                dispatch_created=True,
                backend_triggered=True,
                deliverable_refs=("pr:42",),
                review_complete=True,
            )
        )
