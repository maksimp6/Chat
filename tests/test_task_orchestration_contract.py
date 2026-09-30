"""RED contract tests for issue #634.

TaskPacket schema, single-active-task rule, and stage-transition contract.

All tests in this file are intentionally RED: the production module
``agent_office.orchestration_contract`` does not exist yet. These tests define
the expected public surface so the Team Lead review can validate the contract
before any implementation starts.

Uses the merged canonical task-state model from agent_office.task_state (#614/#632)
and the lifecycle stage vocabulary from agent_office.dispatch_model. Neither is
redefined here.
"""

from __future__ import annotations

import pytest

# This import is intentionally unresolvable until implementation is written.
from agent_office.orchestration_contract import (
    BriefOpinion,
    DuplicateActiveTaskError,
    MissingEvidenceError,
    OrchestratedTaskPacket,
    StaleReviewError,
    TaskRegistry,
    derive_active_task_key,
    next_stage_after,
)
from agent_office.dispatch_model import CANONICAL_LIFECYCLE_STAGES
from agent_office.task_state import AgentTaskEvidence, derive_agent_task_state
from agent_context import EvidenceRef


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _ref(kind: str = "issue", ref: str = "634", version: str = "v1") -> EvidenceRef:
    return EvidenceRef(kind=kind, ref=ref, version=version)


def _packet(
    *,
    task_id: str = "task-001",
    work_item_ref: str = "issues/634",
    stage: str = "contract",
    backend: str = "claude-direct",
    accepted_contract_head: str = "abc123",
    base_ref: str = "abc123",
    current_head: str = "abc123",
    role: str = "test-engineer",
    objective: str = "define orchestration contract",
    expected_deliverable: str = "red test suite",
    evidence_refs: tuple = (),
    approval_boundary: str | None = None,
    brief_opinion: BriefOpinion | None = None,
) -> OrchestratedTaskPacket:
    return OrchestratedTaskPacket(
        task_id=task_id,
        work_item_ref=work_item_ref,
        stage=stage,
        backend=backend,
        accepted_contract_head=accepted_contract_head,
        base_ref=base_ref,
        current_head=current_head,
        role=role,
        objective=objective,
        expected_deliverable=expected_deliverable,
        evidence_refs=evidence_refs,
        approval_boundary=approval_boundary,
        brief_opinion=brief_opinion,
    )


# ===========================================================================
# 1. OrchestratedTaskPacket schema and immutable identity fields
# ===========================================================================


class TestTaskPacketSchema:
    def test_minimal_packet_constructs(self):
        p = _packet()
        assert p.task_id == "task-001"
        assert p.work_item_ref == "issues/634"
        assert p.stage == "contract"
        assert p.backend == "claude-direct"

    def test_task_id_is_required(self):
        with pytest.raises((TypeError, ValueError)):
            OrchestratedTaskPacket(
                work_item_ref="issues/1",
                stage="contract",
                backend="claude-direct",
                accepted_contract_head="abc",
                base_ref="abc",
                current_head="abc",
                role="test-engineer",
                objective="x",
                expected_deliverable="y",
            )

    def test_work_item_ref_is_required(self):
        with pytest.raises((TypeError, ValueError)):
            OrchestratedTaskPacket(
                task_id="t1",
                stage="contract",
                backend="claude-direct",
                accepted_contract_head="abc",
                base_ref="abc",
                current_head="abc",
                role="test-engineer",
                objective="x",
                expected_deliverable="y",
            )

    def test_stage_must_be_canonical(self):
        with pytest.raises(ValueError):
            _packet(stage="planning")

    def test_stage_accepts_every_canonical_stage(self):
        for stage in CANONICAL_LIFECYCLE_STAGES:
            p = _packet(stage=stage)
            assert p.stage == stage

    def test_backend_must_be_known(self):
        with pytest.raises(ValueError):
            _packet(backend="imaginary-backend")

    def test_accepted_contract_head_required(self):
        with pytest.raises((TypeError, ValueError)):
            OrchestratedTaskPacket(
                task_id="t1",
                work_item_ref="issues/1",
                stage="contract",
                backend="claude-direct",
                base_ref="abc",
                current_head="abc",
                role="test-engineer",
                objective="x",
                expected_deliverable="y",
            )

    def test_base_ref_required(self):
        with pytest.raises((TypeError, ValueError)):
            OrchestratedTaskPacket(
                task_id="t1",
                work_item_ref="issues/1",
                stage="contract",
                backend="claude-direct",
                accepted_contract_head="abc",
                current_head="abc",
                role="test-engineer",
                objective="x",
                expected_deliverable="y",
            )

    def test_current_head_required(self):
        with pytest.raises((TypeError, ValueError)):
            OrchestratedTaskPacket(
                task_id="t1",
                work_item_ref="issues/1",
                stage="contract",
                backend="claude-direct",
                accepted_contract_head="abc",
                base_ref="abc",
                role="test-engineer",
                objective="x",
                expected_deliverable="y",
            )

    def test_identity_fields_are_immutable(self):
        p = _packet()
        for field_name in ("task_id", "work_item_ref", "stage", "backend"):
            with pytest.raises((AttributeError, TypeError)):
                object.__setattr__(p, field_name, "mutated")

    def test_active_task_key_is_derived_from_work_item_and_stage(self):
        p = _packet(work_item_ref="issues/99", stage="contract")
        expected = derive_active_task_key("issues/99", "contract")
        assert p.active_task_key == expected

    def test_approval_boundary_optional(self):
        p = _packet(approval_boundary="security-review-required")
        assert p.approval_boundary == "security-review-required"

        p_none = _packet(approval_boundary=None)
        assert p_none.approval_boundary is None

    def test_brief_opinion_optional_default_none(self):
        p = _packet()
        assert p.brief_opinion is None

    def test_brief_opinion_stored_when_supplied(self):
        opinion = BriefOpinion(
            likes="clean design",
            concerns="scope too large",
            improvement="split into two stages",
        )
        p = _packet(brief_opinion=opinion)
        assert p.brief_opinion is opinion

    def test_evidence_refs_stored(self):
        refs = (_ref("issue", "634"), _ref("pr", "632"))
        p = _packet(evidence_refs=refs)
        assert len(p.evidence_refs) == 2


# ===========================================================================
# 2. active_task_key derivation
# ===========================================================================


class TestActiveTaskKeyDerivation:
    def test_same_inputs_produce_same_key(self):
        assert derive_active_task_key("issues/100", "contract") == derive_active_task_key(
            "issues/100", "contract"
        )

    def test_different_stage_produces_different_key(self):
        k1 = derive_active_task_key("issues/100", "contract")
        k2 = derive_active_task_key("issues/100", "implementation")
        assert k1 != k2

    def test_different_work_item_produces_different_key(self):
        k1 = derive_active_task_key("issues/100", "contract")
        k2 = derive_active_task_key("issues/200", "contract")
        assert k1 != k2

    def test_key_is_non_empty_string(self):
        k = derive_active_task_key("issues/1", "contract")
        assert isinstance(k, str)
        assert len(k) > 0

    def test_packet_active_task_key_matches_pure_function(self):
        p = _packet(work_item_ref="issues/55", stage="verification")
        assert p.active_task_key == derive_active_task_key("issues/55", "verification")


# ===========================================================================
# 3. Single-active-task rule (TaskRegistry)
# ===========================================================================


class TestSingleActiveTaskRule:
    def test_first_registration_succeeds(self):
        registry = TaskRegistry()
        registry.register(_packet(task_id="t1"))

    def test_second_non_terminal_task_for_same_key_is_rejected(self):
        registry = TaskRegistry()
        registry.register(_packet(task_id="t1", work_item_ref="issues/1", stage="contract"))
        with pytest.raises(DuplicateActiveTaskError):
            registry.register(_packet(task_id="t2", work_item_ref="issues/1", stage="contract"))

    def test_different_stages_may_coexist(self):
        registry = TaskRegistry()
        registry.register(_packet(task_id="t1", work_item_ref="issues/1", stage="contract"))
        registry.register(_packet(task_id="t2", work_item_ref="issues/1", stage="implementation"))

    def test_different_work_items_may_coexist(self):
        registry = TaskRegistry()
        registry.register(_packet(task_id="t1", work_item_ref="issues/1", stage="contract"))
        registry.register(_packet(task_id="t2", work_item_ref="issues/2", stage="contract"))

    def test_continuation_of_same_task_id_is_idempotent(self):
        registry = TaskRegistry()
        p = _packet(task_id="t1")
        registry.register(p)
        registry.register(p)

    def test_terminal_task_allows_next_stage_dispatch(self):
        registry = TaskRegistry()
        p = _packet(task_id="t1", stage="contract")
        registry.register(p)
        registry.record_terminal(
            task_id="t1",
            state="done",
            evidence_refs=(_ref("pr", "42"),),
        )
        registry.register(_packet(task_id="t2", stage="implementation"))

    def test_second_duplicate_is_not_authoritative(self):
        registry = TaskRegistry()
        registry.register(_packet(task_id="t1", work_item_ref="issues/1", stage="contract"))
        with pytest.raises(DuplicateActiveTaskError):
            registry.register(_packet(task_id="t2", work_item_ref="issues/1", stage="contract"))
        assert registry.active_task_id_for("issues/1", "contract") == "t1"


# ===========================================================================
# 4. New-evidence requirement for retry after terminal state
# ===========================================================================


class TestNewEvidenceRequirement:
    def test_retry_after_failure_without_new_evidence_is_rejected(self):
        registry = TaskRegistry()
        original = (_ref("issue", "634"),)
        p = _packet(task_id="t1", evidence_refs=original)
        registry.register(p)
        registry.record_terminal(task_id="t1", state="failed", evidence_refs=original)

        with pytest.raises(MissingEvidenceError):
            registry.register(
                _packet(task_id="t2", work_item_ref=p.work_item_ref, stage=p.stage,
                        evidence_refs=original)
            )

    def test_retry_after_stall_without_new_evidence_is_rejected(self):
        registry = TaskRegistry()
        original = (_ref("issue", "634"),)
        p = _packet(task_id="t1", evidence_refs=original)
        registry.register(p)
        registry.record_terminal(task_id="t1", state="stalled", evidence_refs=original)

        with pytest.raises(MissingEvidenceError):
            registry.register(
                _packet(task_id="t2", work_item_ref=p.work_item_ref, stage=p.stage,
                        evidence_refs=original)
            )

    def test_retry_after_failure_with_new_evidence_is_accepted(self):
        registry = TaskRegistry()
        original = (_ref("issue", "634"),)
        p = _packet(task_id="t1", evidence_refs=original)
        registry.register(p)
        registry.record_terminal(task_id="t1", state="failed", evidence_refs=original)

        new_evidence = original + (_ref("pr", "999"),)
        registry.register(
            _packet(task_id="t2", work_item_ref=p.work_item_ref, stage=p.stage,
                    evidence_refs=new_evidence)
        )

    def test_continuation_does_not_require_new_evidence(self):
        registry = TaskRegistry()
        original = (_ref("issue", "634"),)
        p = _packet(task_id="t1", evidence_refs=original)
        registry.register(p)
        registry.register(p)


# ===========================================================================
# 5. Exact-head / base-ref contract inheritance
# ===========================================================================


class TestBaseRefContractInheritance:
    def test_implementation_base_ref_must_match_accepted_contract_head(self):
        with pytest.raises(ValueError, match="base_ref"):
            _packet(
                stage="implementation",
                role="backend-engineer",
                accepted_contract_head="contract-sha",
                base_ref="some-other-sha",
                current_head="impl-sha",
            )

    def test_implementation_with_matching_base_ref_is_valid(self):
        p = _packet(
            stage="implementation",
            role="backend-engineer",
            accepted_contract_head="contract-sha",
            base_ref="contract-sha",
            current_head="impl-sha",
        )
        assert p.base_ref == p.accepted_contract_head

    def test_contract_stage_base_ref_not_constrained_to_accepted_head(self):
        p = _packet(
            stage="contract",
            accepted_contract_head="initial-sha",
            base_ref="master-sha",
            current_head="master-sha",
        )
        assert p.stage == "contract"

    def test_current_head_may_differ_from_accepted_contract_head(self):
        p = _packet(
            stage="implementation",
            role="backend-engineer",
            accepted_contract_head="contract-sha",
            base_ref="contract-sha",
            current_head="impl-sha-after-push",
        )
        assert p.current_head != p.accepted_contract_head


# ===========================================================================
# 6. Deterministic stage transitions
# ===========================================================================


class TestStageTransitions:
    def test_accepted_contract_transitions_to_implementation(self):
        assert next_stage_after("contract", "ACCEPTED") == "implementation"

    def test_implementation_success_transitions_to_solution_review(self):
        assert next_stage_after("implementation", "success") == "solution-review"

    def test_accepted_solution_review_transitions_to_maintain(self):
        assert next_stage_after("solution-review", "ACCEPTED") == "maintain"

    def test_ready_maintain_outcome_is_terminal(self):
        # No automatic forward stage beyond maintain; merge action takes over.
        assert next_stage_after("maintain", "READY") is None

    @pytest.mark.parametrize("outcome", ["BLOCKED", "failed", "stalled", "cancelled"])
    def test_terminal_outcomes_stop_all_transitions(self, outcome):
        for stage in CANONICAL_LIFECYCLE_STAGES:
            result = next_stage_after(stage, outcome)
            assert result is None, (
                f"next_stage_after({stage!r}, {outcome!r}) returned {result!r}; expected None"
            )

    def test_transition_result_is_canonical_or_none(self):
        for stage in CANONICAL_LIFECYCLE_STAGES:
            for outcome in ("ACCEPTED", "CHANGES_REQUESTED", "BLOCKED", "success", "failed"):
                result = next_stage_after(stage, outcome)
                assert result is None or result in CANONICAL_LIFECYCLE_STAGES

    def test_full_forward_path_is_deterministic(self):
        forward = [
            ("contract", "ACCEPTED", "implementation"),
            ("implementation", "success", "solution-review"),
            ("solution-review", "ACCEPTED", "maintain"),
        ]
        for stage, outcome, expected in forward:
            result = next_stage_after(stage, outcome)
            assert result == expected, (
                f"next_stage_after({stage!r}, {outcome!r}) = {result!r}; expected {expected!r}"
            )

    def test_unknown_outcome_returns_none(self):
        assert next_stage_after("contract", "WHATS_THIS") is None


# ===========================================================================
# 7. CHANGES_REQUESTED returns to implementation and invalidates stale review
# ===========================================================================


class TestChangesRequestedTransition:
    def test_changes_requested_from_solution_review_returns_to_implementation(self):
        assert next_stage_after("solution-review", "CHANGES_REQUESTED") == "implementation"

    def test_changes_requested_does_not_advance_past_implementation(self):
        result = next_stage_after("solution-review", "CHANGES_REQUESTED")
        assert result not in ("maintain", "solution-review")

    def test_head_mismatch_on_existing_solution_review_raises(self):
        # The existing assert_solution_review_accepted_for_head already detects head mismatch;
        # the orchestration layer must propagate or wrap this as StaleReviewError.
        from agent_office.dispatch_model import SolutionReviewArtifact
        artifact = SolutionReviewArtifact(
            outcome="ACCEPTED",
            reviewed_head_sha="old-sha",
            reviewer_role="team-lead",
        )
        registry = TaskRegistry()
        p = _packet(
            task_id="t-impl",
            stage="implementation",
            role="backend-engineer",
            accepted_contract_head="contract-sha",
            base_ref="contract-sha",
            current_head="new-sha-after-push",
        )
        registry.register(p)
        registry.record_solution_review(
            work_item_ref=p.work_item_ref,
            reviewed_head_sha="old-sha",
            outcome="ACCEPTED",
            reviewer_role="team-lead",
        )
        with pytest.raises(StaleReviewError):
            registry.assert_ready_to_merge(
                work_item_ref=p.work_item_ref,
                current_head="new-sha-after-push",
            )

    def test_implementation_redispatch_after_changes_requested(self):
        # After CHANGES_REQUESTED, a fresh implementation packet for the same item
        # must be accepted (the prior solution-review task is terminal/stale).
        registry = TaskRegistry()
        p_impl_v1 = _packet(
            task_id="t-impl-v1",
            stage="implementation",
            role="backend-engineer",
            accepted_contract_head="contract-sha",
            base_ref="contract-sha",
            current_head="impl-sha-v1",
        )
        registry.register(p_impl_v1)
        registry.record_terminal(
            task_id="t-impl-v1",
            state="done",
            evidence_refs=(_ref("pr", "10"),),
        )

        p_review = _packet(
            task_id="t-review",
            stage="solution-review",
            role="team-lead",
            accepted_contract_head="contract-sha",
            base_ref="contract-sha",
            current_head="impl-sha-v1",
        )
        registry.register(p_review)
        registry.record_terminal(
            task_id="t-review",
            state="done",  # CHANGES_REQUESTED outcome
            evidence_refs=(_ref("pr-review", "10"),),
        )

        p_impl_v2 = _packet(
            task_id="t-impl-v2",
            stage="implementation",
            role="backend-engineer",
            accepted_contract_head="contract-sha",
            base_ref="contract-sha",
            current_head="impl-sha-v2",
            evidence_refs=(_ref("pr-review", "10"), _ref("pr", "10")),
        )
        registry.register(p_impl_v2)


# ===========================================================================
# 8. BriefOpinion is advisory and cannot alter state or transition decisions
# ===========================================================================


class TestBriefOpinionAdvisory:
    def test_opinion_does_not_change_next_stage(self):
        # next_stage_after has no opinion parameter; result is identical with or without opinion.
        assert next_stage_after("contract", "ACCEPTED") == "implementation"

    def test_packet_with_and_without_opinion_have_same_active_task_key(self):
        p_plain = _packet(work_item_ref="issues/1", stage="contract")
        opinion = BriefOpinion(likes="clean", concerns="scope", improvement="split")
        p_opinion = _packet(work_item_ref="issues/1", stage="contract", brief_opinion=opinion)
        assert p_plain.active_task_key == p_opinion.active_task_key

    def test_opinion_does_not_count_as_new_evidence_for_retry(self):
        registry = TaskRegistry()
        original = (_ref("issue", "634"),)
        p = _packet(task_id="t1", evidence_refs=original)
        registry.register(p)
        registry.record_terminal(task_id="t1", state="failed", evidence_refs=original)

        opinion = BriefOpinion(likes="x", concerns="y", improvement="z")
        retry = _packet(
            task_id="t2",
            work_item_ref=p.work_item_ref,
            stage=p.stage,
            evidence_refs=original,
            brief_opinion=opinion,
        )
        with pytest.raises(MissingEvidenceError):
            registry.register(retry)

    def test_brief_opinion_exposes_three_advisory_fields(self):
        opinion = BriefOpinion(
            likes="clean interface",
            concerns="ambiguous error handling",
            improvement="add explicit validation error types",
        )
        assert opinion.likes == "clean interface"
        assert opinion.concerns == "ambiguous error handling"
        assert opinion.improvement == "add explicit validation error types"

    def test_brief_opinion_fields_are_all_optional(self):
        opinion = BriefOpinion()
        for attr in ("likes", "concerns", "improvement"):
            value = getattr(opinion, attr)
            assert value is None or value == ""

    def test_canonical_state_machine_ignores_opinion(self):
        # derive_agent_task_state is evidence-driven; it accepts no opinion argument.
        evidence = AgentTaskEvidence(
            dispatch_created=True,
            backend_triggered=True,
            backend_status="completed",
            deliverable_refs=("pr:42",),
            validation_status="success",
            review_complete=True,
        )
        assert derive_agent_task_state(evidence) == "done"


# ===========================================================================
# 9. Integration: orchestration contract uses the canonical state machine
# ===========================================================================


class TestCanonicalStateMachineIntegration:
    def test_terminal_state_names_stop_forward_transitions(self):
        # Terminal state names from the canonical model (task_state.py) must also
        # stop forward transitions in the orchestration layer.
        for outcome in ("done", "failed", "blocked", "cancelled", "stalled"):
            result = next_stage_after("contract", outcome)
            assert result is None, (
                f"next_stage_after('contract', {outcome!r}) = {result!r}; expected None"
            )

    def test_next_stage_after_handles_all_canonical_stages_without_raising(self):
        for stage in CANONICAL_LIFECYCLE_STAGES:
            next_stage_after(stage, "ACCEPTED")

    def test_stage_returned_by_next_stage_after_is_always_canonical(self):
        outcomes = ("ACCEPTED", "success", "CHANGES_REQUESTED")
        for stage in CANONICAL_LIFECYCLE_STAGES:
            for outcome in outcomes:
                result = next_stage_after(stage, outcome)
                assert result is None or result in CANONICAL_LIFECYCLE_STAGES, (
                    f"next_stage_after({stage!r}, {outcome!r}) = {result!r} is not canonical"
                )
