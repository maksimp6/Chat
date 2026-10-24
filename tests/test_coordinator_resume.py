"""RED contract tests for the event-driven Coordinator auto-resume boundary (#701).

Tests define the behavioral contract for ``agent_office.coordinator_resume``: the
single event-driven boundary that consumes trusted GitHub evidence and calls
``advance_once`` to progress an active work item's lifecycle exactly once per
material event.

ALL tests are expected to FAIL (RED) until the production implementation is added
in a separate PR by Infra/Backend Engineer. Do not add implementation code here.

Expected public symbols from ``agent_office.coordinator_resume``:
  - ``CoordinatorResumeEvent``  — trusted GitHub event wrapper
  - ``ResumeKey``               — canonical idempotency key
  - ``PersistedLifecycleState`` — snapshot of an active work item
  - ``AdvanceResult``           — outcome of a single ``advance_once`` call
  - ``AdvanceOutcome``          — enum of possible outcomes
  - ``advance_once(event, state) -> AdvanceResult``
  - ``AmbiguousEvidenceError``  — raised when material verdicts conflict

Existing types reused (not redefined here):
  - ``agent_office.dispatch_model.SolutionReviewArtifact``
  - ``agent_office.dispatch_model.CANONICAL_LIFECYCLE_STAGES``
  - ``agent_office.dispatch_model.VALID_REVIEW_OUTCOMES``
  - ``agent_office.task_state.AgentTaskEvidence``
  - ``agent_office.task_state.derive_agent_task_state``
"""

from __future__ import annotations

import pytest

from agent_office.coordinator_resume import (
    advance_once,
    AdvanceOutcome,
    AdvanceResult,
    AmbiguousEvidenceError,
    CoordinatorResumeEvent,
    PersistedLifecycleState,
    ResumeKey,
)
from agent_office.dispatch_model import (
    CANONICAL_LIFECYCLE_STAGES,
    SolutionReviewArtifact,
    VALID_REVIEW_OUTCOMES,
)
from agent_office.task_state import AgentTaskEvidence, derive_agent_task_state


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_REPO = "maksimp6/Chat"
_WORK_ITEM = 701
_AUTH_HEAD = "abc123def456deadbeef"  # authoritative head SHA
_STALE_HEAD = "stale000old111aaa222"  # a head SHA that is no longer authoritative
_EVIDENCE_CI = "run:workflow-100"    # unique evidence identity for a CI run
_EVIDENCE_CI_2 = "run:workflow-200"  # a second, distinct CI run identity


def _state(
    current_stage: str = "implementation",
    authoritative_head: str = _AUTH_HEAD,
    last_seen_evidence_identity: str | None = None,
    dispatched_task_ids: tuple = (),
    review_artifact: SolutionReviewArtifact | None = None,
    is_terminal: bool = False,
    requires_owner_approval: bool = False,
    current_task_state: str | None = None,
) -> PersistedLifecycleState:
    """Factory for a minimal deterministic PersistedLifecycleState."""
    return PersistedLifecycleState(
        work_item=_WORK_ITEM,
        current_stage=current_stage,
        authoritative_head=authoritative_head,
        last_seen_evidence_identity=last_seen_evidence_identity,
        dispatched_task_ids=dispatched_task_ids,
        review_artifact=review_artifact,
        is_terminal=is_terminal,
        requires_owner_approval=requires_owner_approval,
        current_task_state=current_task_state,
    )


def _ci_event(
    head_sha: str = _AUTH_HEAD,
    evidence_identity: str = _EVIDENCE_CI,
    conclusion: str = "success",
    repository: str = _REPO,
    work_item: int = _WORK_ITEM,
    is_default_branch_code: bool = True,
) -> CoordinatorResumeEvent:
    """Factory for a minimal trusted workflow_run event."""
    return CoordinatorResumeEvent(
        repository=repository,
        work_item=work_item,
        event_type="workflow_run",
        head_sha=head_sha,
        evidence_identity=evidence_identity,
        payload={"conclusion": conclusion, "status": "completed"},
        is_default_branch_code=is_default_branch_code,
    )


def _review_event(
    head_sha: str = _AUTH_HEAD,
    outcome: str = "ACCEPTED",
    reviewer_role: str = "team-lead",
    evidence_identity: str = "review:pr-99:team-lead",
) -> CoordinatorResumeEvent:
    """Factory for a trusted pull_request_review event."""
    return CoordinatorResumeEvent(
        repository=_REPO,
        work_item=_WORK_ITEM,
        event_type="pull_request_review",
        head_sha=head_sha,
        evidence_identity=evidence_identity,
        payload={"outcome": outcome, "reviewer_role": reviewer_role},
        is_default_branch_code=True,
    )


# ---------------------------------------------------------------------------
# 1. Public API surface
# ---------------------------------------------------------------------------


def test_advance_once_is_callable():
    """advance_once must be a callable importable from agent_office.coordinator_resume."""
    assert callable(advance_once)


def test_resume_key_is_a_data_type():
    """ResumeKey must be constructable with four required fields."""
    key = ResumeKey(
        work_item=_WORK_ITEM,
        lifecycle_stage="implementation",
        authoritative_head=_AUTH_HEAD,
        material_evidence_identity=_EVIDENCE_CI,
    )
    assert key.work_item == _WORK_ITEM
    assert key.lifecycle_stage == "implementation"
    assert key.authoritative_head == _AUTH_HEAD
    assert key.material_evidence_identity == _EVIDENCE_CI


def test_coordinator_resume_event_is_constructable():
    """CoordinatorResumeEvent must accept all required fields without error."""
    event = _ci_event()
    assert event.repository == _REPO
    assert event.work_item == _WORK_ITEM
    assert event.event_type == "workflow_run"
    assert event.head_sha == _AUTH_HEAD


def test_persisted_lifecycle_state_is_constructable():
    """PersistedLifecycleState must be constructable with required fields."""
    state = _state()
    assert state.work_item == _WORK_ITEM
    assert state.current_stage == "implementation"
    assert state.authoritative_head == _AUTH_HEAD
    assert state.is_terminal is False


def test_advance_outcome_has_required_values():
    """AdvanceOutcome must expose at minimum five outcome labels."""
    required = {"DISPATCHED", "BLOCKED", "APPROVAL_REQUIRED", "NO_OP", "TERMINAL"}
    actual = {o.value if hasattr(o, "value") else o for o in AdvanceOutcome}
    assert required.issubset(actual), (
        f"AdvanceOutcome is missing values; required {required}, got {actual}"
    )


def test_advance_result_exposes_outcome_and_reason():
    """AdvanceResult must carry at minimum outcome and reason fields."""
    # Just check the type is constructable; production code fills these fields
    result = AdvanceResult(
        outcome=AdvanceOutcome.NO_OP,
        resume_key=None,
        dispatched_stage=None,
        dispatched_role=None,
        reason="no new evidence",
    )
    assert result.outcome == AdvanceOutcome.NO_OP
    assert isinstance(result.reason, str)


def test_ambiguous_evidence_error_is_an_exception():
    """AmbiguousEvidenceError must be a subclass of Exception."""
    assert issubclass(AmbiguousEvidenceError, Exception)


# ---------------------------------------------------------------------------
# 2. ResumeKey canonical identity
# ---------------------------------------------------------------------------


def test_resume_key_equality_requires_all_four_components():
    """Two ResumeKeys are equal only when all four components match."""
    base = ResumeKey(
        work_item=_WORK_ITEM,
        lifecycle_stage="implementation",
        authoritative_head=_AUTH_HEAD,
        material_evidence_identity=_EVIDENCE_CI,
    )
    same = ResumeKey(
        work_item=_WORK_ITEM,
        lifecycle_stage="implementation",
        authoritative_head=_AUTH_HEAD,
        material_evidence_identity=_EVIDENCE_CI,
    )
    assert base == same


def test_resume_key_differs_when_evidence_identity_differs():
    """A different material_evidence_identity must produce a distinct ResumeKey."""
    key_a = ResumeKey(
        work_item=_WORK_ITEM,
        lifecycle_stage="implementation",
        authoritative_head=_AUTH_HEAD,
        material_evidence_identity=_EVIDENCE_CI,
    )
    key_b = ResumeKey(
        work_item=_WORK_ITEM,
        lifecycle_stage="implementation",
        authoritative_head=_AUTH_HEAD,
        material_evidence_identity=_EVIDENCE_CI_2,
    )
    assert key_a != key_b, "distinct evidence identities must produce distinct ResumeKeys"


def test_resume_key_differs_when_head_differs():
    """A different authoritative_head must produce a distinct ResumeKey."""
    key_current = ResumeKey(
        work_item=_WORK_ITEM,
        lifecycle_stage="verification",
        authoritative_head=_AUTH_HEAD,
        material_evidence_identity=_EVIDENCE_CI,
    )
    key_stale = ResumeKey(
        work_item=_WORK_ITEM,
        lifecycle_stage="verification",
        authoritative_head=_STALE_HEAD,
        material_evidence_identity=_EVIDENCE_CI,
    )
    assert key_current != key_stale, "distinct head SHAs must produce distinct ResumeKeys"


# ---------------------------------------------------------------------------
# 3. Authoritative workflow success advances exactly once
# ---------------------------------------------------------------------------


def test_workflow_success_on_auth_head_advances_to_verification():
    """workflow_run success for exact authoritative head at implementation stage → verification."""
    state = _state(current_stage="implementation", last_seen_evidence_identity=None)
    event = _ci_event(head_sha=_AUTH_HEAD, conclusion="success", evidence_identity=_EVIDENCE_CI)

    result = advance_once(event, state)

    assert result.outcome == AdvanceOutcome.DISPATCHED, (
        f"expected DISPATCHED, got {result.outcome!r}: {result.reason}"
    )
    assert result.dispatched_stage == "verification", (
        f"expected next stage 'verification', got {result.dispatched_stage!r}"
    )


def test_advance_once_returns_exactly_one_dispatched_stage():
    """advance_once must not dispatch more than one stage in a single call."""
    state = _state(current_stage="implementation", last_seen_evidence_identity=None)
    event = _ci_event(head_sha=_AUTH_HEAD, conclusion="success")

    result = advance_once(event, state)

    assert result.outcome == AdvanceOutcome.DISPATCHED
    # dispatched_stage must be a single canonical stage, not a list or None
    assert result.dispatched_stage in CANONICAL_LIFECYCLE_STAGES, (
        f"dispatched_stage {result.dispatched_stage!r} is not in CANONICAL_LIFECYCLE_STAGES"
    )


def test_advance_once_result_carries_resume_key_on_dispatch():
    """AdvanceResult must carry a populated ResumeKey when outcome is DISPATCHED."""
    state = _state(current_stage="implementation", last_seen_evidence_identity=None)
    event = _ci_event(head_sha=_AUTH_HEAD, conclusion="success", evidence_identity=_EVIDENCE_CI)

    result = advance_once(event, state)

    assert result.outcome == AdvanceOutcome.DISPATCHED
    assert result.resume_key is not None, "resume_key must not be None on DISPATCHED outcome"
    assert result.resume_key.work_item == _WORK_ITEM
    assert result.resume_key.authoritative_head == _AUTH_HEAD
    assert result.resume_key.material_evidence_identity == _EVIDENCE_CI


# ---------------------------------------------------------------------------
# 4. Duplicate / replayed event causes no second dispatch
# ---------------------------------------------------------------------------


def test_duplicate_event_with_same_evidence_identity_is_noop():
    """advance_once with already-seen evidence_identity must return NO_OP, not DISPATCHED."""
    state = _state(
        current_stage="implementation",
        last_seen_evidence_identity=_EVIDENCE_CI,  # already processed
    )
    event = _ci_event(head_sha=_AUTH_HEAD, evidence_identity=_EVIDENCE_CI)

    result = advance_once(event, state)

    assert result.outcome == AdvanceOutcome.NO_OP, (
        f"duplicate event must be NO_OP, got {result.outcome!r}: {result.reason}"
    )


def test_replayed_event_does_not_produce_dispatched_outcome():
    """A replayed CoordinatorResumeEvent must never produce DISPATCHED."""
    # Simulate persisted state that already recorded this evidence
    state = _state(
        current_stage="verification",
        last_seen_evidence_identity=_EVIDENCE_CI,
        dispatched_task_ids=("task-01",),
    )
    event = _ci_event(evidence_identity=_EVIDENCE_CI)

    result = advance_once(event, state)

    assert result.outcome is not AdvanceOutcome.DISPATCHED, (
        "replayed event must not produce a second dispatch"
    )


# ---------------------------------------------------------------------------
# 5. Stale-head green CI does not advance
# ---------------------------------------------------------------------------


def test_stale_head_workflow_success_is_noop():
    """workflow_run success for a head SHA that does not match authoritative_head → NO_OP."""
    state = _state(current_stage="implementation", authoritative_head=_AUTH_HEAD)
    event = _ci_event(head_sha=_STALE_HEAD, conclusion="success")  # stale SHA

    result = advance_once(event, state)

    assert result.outcome == AdvanceOutcome.NO_OP, (
        f"stale-head event must be NO_OP, got {result.outcome!r}: {result.reason}"
    )


def test_stale_head_does_not_satisfy_exact_head_requirement():
    """The stale head's evidence must be distinct from the authoritative head's evidence."""
    assert _STALE_HEAD != _AUTH_HEAD, "fixture error: stale and auth heads must differ"

    state = _state(authoritative_head=_AUTH_HEAD)
    stale_event = _ci_event(head_sha=_STALE_HEAD, evidence_identity="run:stale-ci")
    current_event = _ci_event(head_sha=_AUTH_HEAD, evidence_identity=_EVIDENCE_CI)

    stale_result = advance_once(stale_event, state)
    current_result = advance_once(current_event, state)

    assert stale_result.outcome == AdvanceOutcome.NO_OP, "stale head must be NO_OP"
    assert current_result.outcome == AdvanceOutcome.DISPATCHED, "current head must DISPATCH"


# ---------------------------------------------------------------------------
# 6. Unrelated workflow / comment does not advance
# ---------------------------------------------------------------------------


def test_event_for_different_repository_is_noop():
    """An event from a different repository must not advance the work item."""
    state = _state()
    event = _ci_event(repository="other/repo")

    result = advance_once(event, state)

    assert result.outcome == AdvanceOutcome.NO_OP, (
        f"event for different repo must be NO_OP, got {result.outcome!r}"
    )


def test_event_for_different_work_item_is_noop():
    """An event targeting a different issue/PR number must not advance this work item."""
    state = _state()
    event = _ci_event(work_item=999)  # wrong work item

    result = advance_once(event, state)

    assert result.outcome == AdvanceOutcome.NO_OP, (
        f"event for different work_item must be NO_OP, got {result.outcome!r}"
    )


def test_comment_event_without_material_evidence_is_noop():
    """An issue_comment event not containing verifiable material evidence must be NO_OP."""
    state = _state()
    comment_event = CoordinatorResumeEvent(
        repository=_REPO,
        work_item=_WORK_ITEM,
        event_type="issue_comment",
        head_sha=_AUTH_HEAD,
        evidence_identity="comment:mention-only",
        payload={"body": "@claude-lite please continue"},  # mention alone, no material evidence
        is_default_branch_code=True,
    )

    result = advance_once(comment_event, state)

    assert result.outcome == AdvanceOutcome.NO_OP, (
        f"comment/mention without material evidence must be NO_OP, got {result.outcome!r}"
    )


def test_failed_workflow_does_not_advance_forward():
    """A workflow_run event with conclusion='failure' must not advance to the next stage."""
    state = _state(current_stage="implementation", last_seen_evidence_identity=None)
    event = _ci_event(head_sha=_AUTH_HEAD, conclusion="failure")

    result = advance_once(event, state)

    assert result.outcome != AdvanceOutcome.DISPATCHED, (
        "failed CI must not advance lifecycle forward"
    )


# ---------------------------------------------------------------------------
# 7. Exact-head CHANGES_REQUIRED returns to implementation once
# ---------------------------------------------------------------------------


def test_exact_head_changes_requested_returns_to_implementation():
    """CHANGES_REQUESTED review for exact authoritative head → dispatch to implementation."""
    state = _state(
        current_stage="solution-review",
        authoritative_head=_AUTH_HEAD,
        last_seen_evidence_identity=None,
    )
    event = _review_event(
        head_sha=_AUTH_HEAD,
        outcome="CHANGES_REQUESTED",
        evidence_identity="review:pr-99:changes-requested",
    )

    result = advance_once(event, state)

    assert result.outcome == AdvanceOutcome.DISPATCHED, (
        f"CHANGES_REQUESTED for exact head must produce DISPATCHED, got {result.outcome!r}"
    )
    assert result.dispatched_stage == "implementation", (
        f"CHANGES_REQUESTED must return to 'implementation', got {result.dispatched_stage!r}"
    )


def test_changes_requested_dispatches_an_implementation_role():
    """The role dispatched after CHANGES_REQUESTED must be an implementation role."""
    from agent_office.dispatch_model import IMPLEMENTATION_ROLES

    state = _state(current_stage="solution-review", last_seen_evidence_identity=None)
    event = _review_event(outcome="CHANGES_REQUESTED", evidence_identity="review:cr-1")

    result = advance_once(event, state)

    assert result.outcome == AdvanceOutcome.DISPATCHED
    assert result.dispatched_role in IMPLEMENTATION_ROLES, (
        f"CHANGES_REQUESTED must dispatch to an implementation role, got {result.dispatched_role!r}"
    )


def test_stale_head_changes_requested_is_noop():
    """CHANGES_REQUESTED review for a stale head must not dispatch to implementation."""
    state = _state(current_stage="solution-review", authoritative_head=_AUTH_HEAD)
    event = _review_event(
        head_sha=_STALE_HEAD,  # stale head
        outcome="CHANGES_REQUESTED",
        evidence_identity="review:stale-cr",
    )

    result = advance_once(event, state)

    assert result.outcome == AdvanceOutcome.NO_OP, (
        "CHANGES_REQUESTED for stale head must be NO_OP, not dispatch"
    )


# ---------------------------------------------------------------------------
# 8. Follow-up implementation requires genuinely new evidence
# ---------------------------------------------------------------------------


def test_follow_up_implementation_requires_new_evidence():
    """After CHANGES_REQUESTED dispatch, re-presenting the same evidence is NO_OP."""
    evidence_id = "review:cr-dispatch"
    state = _state(
        current_stage="implementation",
        last_seen_evidence_identity=evidence_id,  # already dispatched for this evidence
    )
    event = _review_event(outcome="CHANGES_REQUESTED", evidence_identity=evidence_id)

    result = advance_once(event, state)

    assert result.outcome == AdvanceOutcome.NO_OP, (
        "same CHANGES_REQUESTED evidence presented again must be NO_OP"
    )


def test_new_ci_evidence_after_changes_required_can_advance():
    """Fresh CI evidence on a new commit after CHANGES_REQUESTED must be eligible to advance."""
    new_head = "new_sha_after_fixes_00"
    state = _state(
        current_stage="verification",
        authoritative_head=new_head,
        last_seen_evidence_identity=None,  # no evidence seen for this new head yet
    )
    event = _ci_event(
        head_sha=new_head,
        conclusion="success",
        evidence_identity="run:post-fix-ci-333",
    )

    result = advance_once(event, state)

    # On new head with no prior evidence: eligible for forward advance
    assert result.outcome in (AdvanceOutcome.DISPATCHED, AdvanceOutcome.NO_OP), (
        f"unexpected outcome {result.outcome!r} for new-head fresh-evidence event"
    )
    # Specifically: it must NOT be BLOCKED or TERMINAL on a clean new head
    assert result.outcome not in (AdvanceOutcome.BLOCKED, AdvanceOutcome.TERMINAL), (
        "fresh new-head evidence must not produce BLOCKED or TERMINAL"
    )


# ---------------------------------------------------------------------------
# 9. ACCEPTED + required fresh checks → one Maintainer task, never Coordinator merge
# ---------------------------------------------------------------------------


def test_accepted_review_with_fresh_ci_dispatches_to_maintain():
    """ACCEPTED for exact head with fresh green CI → dispatched_stage='maintain'."""
    accepted_artifact = SolutionReviewArtifact(
        outcome="ACCEPTED",
        reviewed_head_sha=_AUTH_HEAD,
        reviewer_role="team-lead",
    )
    state = _state(
        current_stage="solution-review",
        authoritative_head=_AUTH_HEAD,
        review_artifact=accepted_artifact,
        last_seen_evidence_identity=None,
    )
    event = _ci_event(
        head_sha=_AUTH_HEAD,
        conclusion="success",
        evidence_identity="run:fresh-ci-for-accept",
    )

    result = advance_once(event, state)

    assert result.outcome == AdvanceOutcome.DISPATCHED, (
        f"ACCEPTED + fresh CI must produce DISPATCHED, got {result.outcome!r}: {result.reason}"
    )
    assert result.dispatched_stage == "maintain", (
        f"must dispatch to 'maintain', got {result.dispatched_stage!r}"
    )


def test_accepted_review_dispatches_release_manager_role():
    """The Maintainer task must be dispatched to release-manager, not an implementation role."""
    from agent_office.dispatch_model import IMPLEMENTATION_ROLES

    accepted_artifact = SolutionReviewArtifact(
        outcome="ACCEPTED",
        reviewed_head_sha=_AUTH_HEAD,
        reviewer_role="team-lead",
    )
    state = _state(
        current_stage="solution-review",
        authoritative_head=_AUTH_HEAD,
        review_artifact=accepted_artifact,
        last_seen_evidence_identity=None,
    )
    event = _ci_event(head_sha=_AUTH_HEAD, conclusion="success")

    result = advance_once(event, state)

    assert result.outcome == AdvanceOutcome.DISPATCHED
    assert result.dispatched_role == "release-manager", (
        f"maintain-stage task must be for release-manager, got {result.dispatched_role!r}"
    )
    assert result.dispatched_role not in IMPLEMENTATION_ROLES, (
        "Coordinator must not dispatch an implementation role for the maintain stage"
    )


def test_coordinator_does_not_merge_directly():
    """advance_once must never produce a merge action; it must dispatch to release-manager."""
    accepted_artifact = SolutionReviewArtifact(
        outcome="ACCEPTED",
        reviewed_head_sha=_AUTH_HEAD,
        reviewer_role="team-lead",
    )
    state = _state(
        current_stage="solution-review",
        authoritative_head=_AUTH_HEAD,
        review_artifact=accepted_artifact,
    )
    event = _ci_event(head_sha=_AUTH_HEAD, conclusion="success")

    result = advance_once(event, state)

    # The result must never instruct a direct merge; at most it dispatches to maintain
    assert result.outcome != AdvanceOutcome.TERMINAL or result.dispatched_stage is None, (
        "Coordinator must not record a TERMINAL outcome as if it merged; dispatch to maintain"
    )
    if result.outcome == AdvanceOutcome.DISPATCHED:
        assert result.dispatched_stage == "maintain", (
            "Coordinator may only dispatch to 'maintain', never perform the merge itself"
        )


def test_accepted_without_fresh_ci_does_not_advance_to_maintain():
    """ACCEPTED review artifact alone, without a fresh CI event, must not advance to maintain."""
    accepted_artifact = SolutionReviewArtifact(
        outcome="ACCEPTED",
        reviewed_head_sha=_AUTH_HEAD,
        reviewer_role="team-lead",
    )
    state = _state(
        current_stage="solution-review",
        authoritative_head=_AUTH_HEAD,
        review_artifact=accepted_artifact,
        last_seen_evidence_identity=None,
    )
    # Send only the review event, not a fresh CI success event
    event = _review_event(
        head_sha=_AUTH_HEAD,
        outcome="ACCEPTED",
        evidence_identity="review:accept-only-no-ci",
    )

    result = advance_once(event, state)

    assert result.dispatched_stage != "maintain" or result.outcome != AdvanceOutcome.DISPATCHED, (
        "ACCEPTED review alone without fresh green CI must not advance to maintain"
    )


# ---------------------------------------------------------------------------
# 10. Approval boundary stops for owner
# ---------------------------------------------------------------------------


def test_approval_required_state_does_not_auto_advance():
    """When requires_owner_approval=True the lifecycle must stop and request owner input."""
    state = _state(requires_owner_approval=True)
    event = _ci_event(conclusion="success")

    result = advance_once(event, state)

    assert result.outcome == AdvanceOutcome.APPROVAL_REQUIRED, (
        f"requires_owner_approval state must produce APPROVAL_REQUIRED, got {result.outcome!r}"
    )


def test_approval_required_outcome_does_not_dispatch():
    """An APPROVAL_REQUIRED result must carry no dispatched_stage."""
    state = _state(requires_owner_approval=True)
    event = _ci_event(conclusion="success")

    result = advance_once(event, state)

    assert result.outcome == AdvanceOutcome.APPROVAL_REQUIRED
    assert result.dispatched_stage is None, (
        "APPROVAL_REQUIRED must not also dispatch a stage"
    )


# ---------------------------------------------------------------------------
# 11. BLOCKED / failed / stalled / cancelled stop
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_state", ["blocked", "failed", "stalled", "cancelled"])
def test_terminal_task_state_stops_all_advances(task_state: str):
    """BLOCKED/failed/stalled/cancelled current_task_state must prevent any dispatch."""
    state = _state(current_task_state=task_state)
    event = _ci_event(conclusion="success")

    result = advance_once(event, state)

    assert result.outcome in (AdvanceOutcome.BLOCKED, AdvanceOutcome.NO_OP, AdvanceOutcome.TERMINAL), (
        f"task_state={task_state!r} must stop progression; got {result.outcome!r}: {result.reason}"
    )
    assert result.outcome != AdvanceOutcome.DISPATCHED, (
        f"task_state={task_state!r} must not dispatch a new stage"
    )


def test_is_terminal_state_stops_all_advances():
    """is_terminal=True (e.g. after merge) must produce TERMINAL or NO_OP, never DISPATCHED."""
    state = _state(is_terminal=True)
    event = _ci_event(conclusion="success")

    result = advance_once(event, state)

    assert result.outcome in (AdvanceOutcome.TERMINAL, AdvanceOutcome.NO_OP), (
        f"is_terminal state must produce TERMINAL or NO_OP, got {result.outcome!r}"
    )
    assert result.outcome != AdvanceOutcome.DISPATCHED, (
        "terminal work item must never produce another dispatch"
    )


def test_failed_ci_at_verification_stage_stops_and_does_not_advance():
    """CI failure at verification must not advance to solution-review."""
    state = _state(current_stage="verification", last_seen_evidence_identity=None)
    event = _ci_event(head_sha=_AUTH_HEAD, conclusion="failure")

    result = advance_once(event, state)

    assert result.dispatched_stage != "solution-review", (
        "failed CI at verification must not advance to solution-review"
    )


# ---------------------------------------------------------------------------
# 12. Conflicting / ambiguous material verdicts fail closed
# ---------------------------------------------------------------------------


def test_conflicting_verdicts_raise_ambiguous_evidence_error():
    """Payload with two conflicting outcome verdicts for the same head must raise AmbiguousEvidenceError."""
    state = _state(current_stage="solution-review")

    conflicting_event = CoordinatorResumeEvent(
        repository=_REPO,
        work_item=_WORK_ITEM,
        event_type="pull_request_review",
        head_sha=_AUTH_HEAD,
        evidence_identity="review:conflicting-verdict",
        payload={
            "verdicts": [
                {"reviewer_role": "team-lead", "outcome": "ACCEPTED"},
                {"reviewer_role": "security-reviewer", "outcome": "CHANGES_REQUESTED"},
            ]
        },
        is_default_branch_code=True,
    )

    with pytest.raises(AmbiguousEvidenceError):
        advance_once(conflicting_event, state)


def test_ambiguous_evidence_does_not_silently_advance():
    """Ambiguous evidence must not produce a DISPATCHED result; it must fail closed."""
    state = _state(current_stage="solution-review")

    conflicting_event = CoordinatorResumeEvent(
        repository=_REPO,
        work_item=_WORK_ITEM,
        event_type="pull_request_review",
        head_sha=_AUTH_HEAD,
        evidence_identity="review:ambiguous-2",
        payload={
            "verdicts": [
                {"reviewer_role": "team-lead", "outcome": "BLOCKED"},
                {"reviewer_role": "process-governor", "outcome": "ACCEPTED"},
            ]
        },
        is_default_branch_code=True,
    )

    try:
        result = advance_once(conflicting_event, state)
        # If no exception, the outcome must not be DISPATCHED
        assert result.outcome != AdvanceOutcome.DISPATCHED, (
            "ambiguous verdicts must not produce DISPATCHED outcome"
        )
    except AmbiguousEvidenceError:
        pass  # correct: fail closed


# ---------------------------------------------------------------------------
# 13. Merge completion becomes terminal with no further dispatch
# ---------------------------------------------------------------------------


def test_merge_event_produces_terminal_outcome():
    """A pull_request merged event must produce TERMINAL outcome."""
    state = _state(current_stage="maintain", last_seen_evidence_identity=None)
    merge_event = CoordinatorResumeEvent(
        repository=_REPO,
        work_item=_WORK_ITEM,
        event_type="pull_request",
        head_sha=_AUTH_HEAD,
        evidence_identity="pr:merged:701",
        payload={"action": "closed", "merged": True},
        is_default_branch_code=True,
    )

    result = advance_once(merge_event, state)

    assert result.outcome == AdvanceOutcome.TERMINAL, (
        f"merge event must produce TERMINAL outcome, got {result.outcome!r}: {result.reason}"
    )


def test_terminal_state_after_merge_has_no_dispatched_stage():
    """After merge, AdvanceResult must carry no dispatched_stage."""
    state = _state(current_stage="maintain", is_terminal=False)
    merge_event = CoordinatorResumeEvent(
        repository=_REPO,
        work_item=_WORK_ITEM,
        event_type="pull_request",
        head_sha=_AUTH_HEAD,
        evidence_identity="pr:merged:final",
        payload={"action": "closed", "merged": True},
        is_default_branch_code=True,
    )

    result = advance_once(merge_event, state)

    assert result.outcome == AdvanceOutcome.TERMINAL
    assert result.dispatched_stage is None, (
        "TERMINAL result must not carry a dispatched_stage"
    )


def test_subsequent_event_after_merge_is_noop():
    """Any event arriving after the work item is terminal must be NO_OP."""
    terminal_state = _state(is_terminal=True)
    late_event = _ci_event(conclusion="success", evidence_identity="run:late-ci")

    result = advance_once(late_event, terminal_state)

    assert result.outcome in (AdvanceOutcome.TERMINAL, AdvanceOutcome.NO_OP), (
        f"event after terminal state must be NO_OP or TERMINAL, got {result.outcome!r}"
    )


# ---------------------------------------------------------------------------
# 14. Persisted-state restart / replay idempotency
# ---------------------------------------------------------------------------


def test_replay_with_persisted_state_is_idempotent():
    """Processing the same event twice must yield identical outcomes when state is persisted."""
    state = _state(current_stage="implementation", last_seen_evidence_identity=None)
    event = _ci_event(head_sha=_AUTH_HEAD, conclusion="success", evidence_identity=_EVIDENCE_CI)

    result_first = advance_once(event, state)

    # Simulate persisted state after first advance: evidence is now seen
    persisted_after_first = _state(
        current_stage=result_first.dispatched_stage or state.current_stage,
        last_seen_evidence_identity=_EVIDENCE_CI,
        dispatched_task_ids=(result_first.resume_key.material_evidence_identity,)
        if result_first.resume_key
        else (),
    )

    result_replay = advance_once(event, persisted_after_first)

    assert result_replay.outcome == AdvanceOutcome.NO_OP, (
        f"replaying same event against updated persisted state must be NO_OP, "
        f"got {result_replay.outcome!r}: {result_replay.reason}"
    )


def test_all_events_replayed_from_scratch_yield_same_result():
    """Full replay of all events from persisted state must match original progression."""
    evidence_id_ci = "run:deterministic-ci"
    event = _ci_event(head_sha=_AUTH_HEAD, conclusion="success", evidence_identity=evidence_id_ci)
    state = _state(current_stage="implementation")

    first_pass = advance_once(event, state)
    second_pass = advance_once(event, state)  # same initial state — identical inputs

    # Same event + same state must produce identical results
    assert first_pass.outcome == second_pass.outcome, (
        "identical (event, state) inputs must produce identical outcomes"
    )
    assert first_pass.dispatched_stage == second_pass.dispatched_stage, (
        "identical (event, state) inputs must dispatch the same stage"
    )


def test_no_new_evidence_against_same_persisted_state_is_noop():
    """Calling advance_once with evidence already present in persisted state is always NO_OP."""
    already_seen = "run:already-seen-001"
    state = _state(
        current_stage="verification",
        last_seen_evidence_identity=already_seen,
    )
    event = _ci_event(evidence_identity=already_seen)

    result = advance_once(event, state)

    assert result.outcome == AdvanceOutcome.NO_OP, (
        "already-seen evidence must produce NO_OP, preventing re-dispatch"
    )


# ---------------------------------------------------------------------------
# 15. Security / economy assertions
# ---------------------------------------------------------------------------


def test_event_with_write_capability_requires_default_branch_code():
    """A workflow_run event with is_default_branch_code=False must not be treated as execution evidence."""
    state = _state()
    pr_head_event = _ci_event(
        head_sha=_AUTH_HEAD,
        conclusion="success",
        is_default_branch_code=False,  # PR-head code, not trusted default-branch
    )

    result = advance_once(pr_head_event, state)

    assert result.outcome != AdvanceOutcome.DISPATCHED, (
        "event with is_default_branch_code=False must not dispatch; "
        "write-capable actions must only execute trusted default-branch code"
    )


def test_comment_mention_alone_is_not_execution_evidence():
    """An issue_comment event containing only a mention must not count as material evidence."""
    state = _state()
    mention_only = CoordinatorResumeEvent(
        repository=_REPO,
        work_item=_WORK_ITEM,
        event_type="issue_comment",
        head_sha=_AUTH_HEAD,
        evidence_identity="comment:mention-coordinator-continue",
        payload={"body": "please @coordinator continue"},  # mention, not a deliverable
        is_default_branch_code=True,
    )

    result = advance_once(mention_only, state)

    assert result.outcome == AdvanceOutcome.NO_OP, (
        "comment/mention alone is not execution evidence and must produce NO_OP"
    )


def test_no_new_evidence_does_not_trigger_paid_model_dispatch():
    """advance_once with no new evidence must return NO_OP, preventing paid-model redispatch."""
    state = _state(last_seen_evidence_identity=_EVIDENCE_CI)
    same_event = _ci_event(evidence_identity=_EVIDENCE_CI)  # already processed

    result = advance_once(same_event, state)

    assert result.outcome == AdvanceOutcome.NO_OP, (
        "no-new-evidence condition must return NO_OP — never launch a paid agent"
    )


def test_resume_key_includes_all_four_required_components():
    """ResumeKey must carry work_item, lifecycle_stage, authoritative_head, material_evidence_identity."""
    key = ResumeKey(
        work_item=_WORK_ITEM,
        lifecycle_stage="verification",
        authoritative_head=_AUTH_HEAD,
        material_evidence_identity=_EVIDENCE_CI,
    )
    assert key.work_item == _WORK_ITEM, "ResumeKey must include work_item"
    assert key.lifecycle_stage == "verification", "ResumeKey must include lifecycle_stage"
    assert key.authoritative_head == _AUTH_HEAD, "ResumeKey must include authoritative_head"
    assert key.material_evidence_identity == _EVIDENCE_CI, (
        "ResumeKey must include material_evidence_identity"
    )


def test_resume_key_is_hashable_for_deduplication():
    """ResumeKey must be hashable so it can be used as a set/dict key for deduplication."""
    key_a = ResumeKey(
        work_item=_WORK_ITEM,
        lifecycle_stage="implementation",
        authoritative_head=_AUTH_HEAD,
        material_evidence_identity=_EVIDENCE_CI,
    )
    key_b = ResumeKey(
        work_item=_WORK_ITEM,
        lifecycle_stage="implementation",
        authoritative_head=_AUTH_HEAD,
        material_evidence_identity=_EVIDENCE_CI,
    )
    seen: set = {key_a}
    assert key_b in seen, "identical ResumeKeys must be recognized as duplicates via hash"


def test_lifecycle_stages_used_in_tests_are_canonical():
    """All lifecycle stage strings referenced in test fixtures must be in CANONICAL_LIFECYCLE_STAGES."""
    stages_under_test = {
        "contract",
        "contract-review",
        "implementation",
        "verification",
        "solution-review",
        "maintain",
    }
    for stage in stages_under_test:
        assert stage in CANONICAL_LIFECYCLE_STAGES, (
            f"stage {stage!r} used in tests is not in CANONICAL_LIFECYCLE_STAGES"
        )


def test_valid_review_outcomes_used_in_tests_are_canonical():
    """All review outcome strings used in test fixtures must be in VALID_REVIEW_OUTCOMES."""
    outcomes_under_test = {"ACCEPTED", "CHANGES_REQUESTED", "BLOCKED"}
    for outcome in outcomes_under_test:
        assert outcome in VALID_REVIEW_OUTCOMES, (
            f"outcome {outcome!r} used in tests is not in VALID_REVIEW_OUTCOMES"
        )
