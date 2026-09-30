"""RED contract tests for issue #634.

TaskPacket schema, single-active-task rule, and stage-transition contract.

All tests in this file are intentionally RED: the production module
``agent_office.orchestration_contract`` does not exist yet. These tests define
the expected public surface so the Team Lead review can validate the contract
before any implementation starts.

Reuses without re-implementing:
- CANONICAL_LIFECYCLE_STAGES, SolutionReviewArtifact,
  SolutionReviewRequiredError, assert_solution_review_accepted_for_head
  from agent_office.dispatch_model
- AgentTaskEvidence, derive_agent_task_state from agent_office.task_state
- EvidenceRef (frozen dataclass with value equality) from agent_context
"""

from __future__ import annotations

import pytest

# Intentionally unresolvable until production implementation is written.
from agent_office.orchestration_contract import (
    BriefOpinion,
    DuplicateActiveTaskError,
    MissingEvidenceError,
    OrchestratedTaskPacket,
    TaskRegistry,
    derive_active_task_key,
    next_stage_after,
)
from agent_office.dispatch_model import (
    CANONICAL_LIFECYCLE_STAGES,
    SolutionReviewArtifact,
    SolutionReviewRequiredError,
    assert_solution_review_accepted_for_head,
)
from agent_office.task_state import AgentTaskEvidence, derive_agent_task_state
from agent_context import EvidenceRef


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _ref(kind: str = "issue", ref: str = "634", version: str = "v1") -> EvidenceRef:
    return EvidenceRef(kind=kind, ref=ref, version=version)


def _contract_provenance(sha: str = "contract-sha") -> EvidenceRef:
    """Evidence that the accepted contract at ``sha`` was materialized in the working base."""
    return EvidenceRef(kind="contract", ref=sha, version="accepted")


def _packet(
    *,
    task_id: str = "task-001",
    work_item_ref: str = "issues/634",
    stage: str = "contract",
    backend: str = "claude-direct",
    accepted_contract_head: str | None = None,
    base_ref: str = "master-sha",
    current_head: str = "master-sha",
    role: str = "test-engineer",
    objective: str = "define orchestration contract",
    expected_deliverable: str = "red test suite",
    evidence_refs: tuple = (),
    approval_boundary: str | None = None,
    brief_opinion: "BriefOpinion | None" = None,
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


def _impl_packet(**kwargs) -> OrchestratedTaskPacket:
    """Valid implementation-stage packet with required contract provenance evidence."""
    defaults: dict = dict(
        stage="implementation",
        role="backend-engineer",
        accepted_contract_head="contract-sha",
        base_ref="master-sha",
        current_head="impl-sha",
        evidence_refs=(_contract_provenance("contract-sha"),),
    )
    defaults.update(kwargs)
    return _packet(**defaults)


# ===========================================================================
# 1. OrchestratedTaskPacket schema and immutable identity fields
# ===========================================================================


class TestTaskPacketSchema:
    def test_minimal_contract_packet_constructs(self):
        p = _packet()
        assert p.task_id == "task-001"
        assert p.work_item_ref == "issues/634"
        assert p.stage == "contract"
        assert p.backend == "claude-direct"

    def test_accepted_contract_head_optional_at_contract_stage(self):
        p = _packet(stage="contract", accepted_contract_head=None)
        assert p.accepted_contract_head is None

    def test_accepted_contract_head_optional_at_contract_review_stage(self):
        p = _packet(stage="contract-review", accepted_contract_head=None)
        assert p.accepted_contract_head is None

    def test_accepted_contract_head_required_at_implementation_stage(self):
        with pytest.raises((TypeError, ValueError)):
            _packet(
                stage="implementation",
                role="backend-engineer",
                accepted_contract_head=None,
                evidence_refs=(_contract_provenance("contract-sha"),),
            )

    def test_task_id_is_required(self):
        with pytest.raises((TypeError, ValueError)):
            OrchestratedTaskPacket(
                work_item_ref="issues/1",
                stage="contract",
                backend="claude-direct",
                accepted_contract_head=None,
                base_ref="master",
                current_head="master",
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
                accepted_contract_head=None,
                base_ref="master",
                current_head="master",
                role="test-engineer",
                objective="x",
                expected_deliverable="y",
            )

    def test_stage_must_be_canonical(self):
        with pytest.raises(ValueError):
            _packet(stage="planning")

    def test_stage_accepts_every_canonical_stage(self):
        for stage in CANONICAL_LIFECYCLE_STAGES:
            if stage in ("contract", "contract-review"):
                p = _packet(stage=stage, accepted_contract_head=None)
            else:
                p = _impl_packet(stage=stage)
            assert p.stage == stage

    def test_backend_must_be_known(self):
        with pytest.raises(ValueError):
            _packet(backend="imaginary-backend")

    def test_base_ref_required(self):
        with pytest.raises((TypeError, ValueError)):
            OrchestratedTaskPacket(
                task_id="t1",
                work_item_ref="issues/1",
                stage="contract",
                backend="claude-direct",
                accepted_contract_head=None,
                current_head="master",
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
                accepted_contract_head=None,
                base_ref="master",
                role="test-engineer",
                objective="x",
                expected_deliverable="y",
            )

    def test_identity_fields_are_immutable(self):
        p = _packet()
        for field_name in ("task_id", "work_item_ref", "stage", "backend"):
            with pytest.raises((AttributeError, TypeError)):
                setattr(p, field_name, "mutated")

    def test_active_task_key_is_derived_from_work_item(self):
        p = _packet(work_item_ref="issues/99", stage="contract")
        assert p.active_task_key == derive_active_task_key("issues/99")

    def test_approval_boundary_optional(self):
        p = _packet(approval_boundary="security-review-required")
        assert p.approval_boundary == "security-review-required"
        p_none = _packet(approval_boundary=None)
        assert p_none.approval_boundary is None

    def test_brief_opinion_optional_default_none(self):
        assert _packet().brief_opinion is None

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
# 2. Contract provenance (EvidenceRef, not base_ref string equality)
# ===========================================================================


class TestContractProvenance:
    def test_implementation_requires_contract_provenance_evidence(self):
        with pytest.raises(ValueError, match="contract provenance"):
            _packet(
                stage="implementation",
                role="backend-engineer",
                accepted_contract_head="contract-sha",
                base_ref="master-sha",
                current_head="impl-sha",
                evidence_refs=(),
            )

    def test_implementation_with_provenance_evidence_is_valid(self):
        p = _impl_packet()
        assert any(e.kind == "contract" for e in p.evidence_refs)

    def test_contract_stage_does_not_require_provenance_evidence(self):
        p = _packet(stage="contract", accepted_contract_head=None, evidence_refs=())
        assert p.stage == "contract"

    def test_contract_review_stage_does_not_require_provenance_evidence(self):
        p = _packet(stage="contract-review", accepted_contract_head=None, evidence_refs=())
        assert p.stage == "contract-review"

    def test_base_ref_may_be_branch_name_not_sha(self):
        # base_ref is a git ref; it is not required to equal accepted_contract_head.
        p = _impl_packet(base_ref="main", current_head="impl-sha")
        assert p.base_ref == "main"

    def test_provenance_evidence_ref_equality_is_value_based(self):
        # EvidenceRef is frozen; same args must compare equal.
        r1 = _contract_provenance("abc123")
        r2 = _contract_provenance("abc123")
        assert r1 == r2

    def test_different_contract_sha_gives_different_provenance(self):
        assert _contract_provenance("sha-1") != _contract_provenance("sha-2")


# ===========================================================================
# 3. active_task_key derivation (work-item-wide, no stage component)
# ===========================================================================


class TestActiveTaskKeyDerivation:
    def test_same_work_item_produces_same_key(self):
        assert derive_active_task_key("issues/100") == derive_active_task_key("issues/100")

    def test_different_work_items_produce_different_keys(self):
        assert derive_active_task_key("issues/100") != derive_active_task_key("issues/200")

    def test_key_is_non_empty_string(self):
        k = derive_active_task_key("issues/1")
        assert isinstance(k, str) and len(k) > 0

    def test_packet_active_task_key_matches_pure_function(self):
        p = _packet(work_item_ref="issues/55", stage="contract")
        assert p.active_task_key == derive_active_task_key("issues/55")

    def test_different_stages_same_work_item_produce_same_key(self):
        # Work-item-wide lock: stage does not affect the key.
        p_contract = _packet(work_item_ref="issues/55", stage="contract")
        p_impl = _impl_packet(work_item_ref="issues/55")
        assert p_contract.active_task_key == p_impl.active_task_key


# ===========================================================================
# 4. Single-active-task rule (one non-terminal per work item across all stages)
# ===========================================================================


class TestSingleActiveTaskRule:
    def test_first_registration_succeeds(self):
        registry = TaskRegistry()
        registry.register(_packet(task_id="t1"))

    def test_second_non_terminal_task_same_stage_is_rejected(self):
        registry = TaskRegistry()
        registry.register(_packet(task_id="t1", work_item_ref="issues/1", stage="contract"))
        with pytest.raises(DuplicateActiveTaskError):
            registry.register(_packet(task_id="t2", work_item_ref="issues/1", stage="contract"))

    def test_implementation_rejected_while_contract_active(self):
        # Stage overlap is forbidden: one non-terminal task per work item.
        registry = TaskRegistry()
        registry.register(_packet(task_id="t1", work_item_ref="issues/1", stage="contract"))
        with pytest.raises(DuplicateActiveTaskError):
            registry.register(_impl_packet(task_id="t2", work_item_ref="issues/1"))

    def test_different_work_items_may_coexist(self):
        registry = TaskRegistry()
        registry.register(_packet(task_id="t1", work_item_ref="issues/1", stage="contract"))
        registry.register(_packet(task_id="t2", work_item_ref="issues/2", stage="contract"))

    def test_continuation_of_same_task_id_is_idempotent(self):
        registry = TaskRegistry()
        p = _packet(task_id="t1")
        registry.register(p)
        registry.register(p)

    def test_contract_red_ready_allows_contract_review_registration(self):
        # After contract terminates with material_outcome=RED_READY,
        # contract-review is the only permitted next stage.
        registry = TaskRegistry()
        p = _packet(task_id="t1", work_item_ref="issues/1", stage="contract")
        registry.register(p)
        registry.record_terminal(
            task_id="t1", state="done", material_outcome="RED_READY",
            evidence_refs=(_ref("pr", "42"),),
        )
        registry.register(
            _packet(task_id="t2", work_item_ref="issues/1", stage="contract-review")
        )

    def test_wrong_stage_after_contract_red_ready_is_rejected(self):
        # contract + RED_READY -> contract-review; skipping to implementation is invalid.
        registry = TaskRegistry()
        p = _packet(task_id="t1", work_item_ref="issues/1", stage="contract")
        registry.register(p)
        registry.record_terminal(
            task_id="t1", state="done", material_outcome="RED_READY",
            evidence_refs=(_ref("pr", "42"),),
        )
        with pytest.raises((DuplicateActiveTaskError, ValueError)):
            registry.register(_impl_packet(task_id="t2", work_item_ref="issues/1"))

    def test_authoritative_task_id_not_replaced_by_duplicate(self):
        registry = TaskRegistry()
        registry.register(_packet(task_id="t1", work_item_ref="issues/1", stage="contract"))
        with pytest.raises(DuplicateActiveTaskError):
            registry.register(_packet(task_id="t2", work_item_ref="issues/1", stage="contract"))
        assert registry.active_task_id_for("issues/1") == "t1"


# ===========================================================================
# 5. New-evidence requirement for retry after terminal failure/stall
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
                _packet(
                    task_id="t2",
                    work_item_ref=p.work_item_ref,
                    stage=p.stage,
                    evidence_refs=original,
                )
            )

    def test_retry_after_stall_without_new_evidence_is_rejected(self):
        registry = TaskRegistry()
        original = (_ref("issue", "634"),)
        p = _packet(task_id="t1", evidence_refs=original)
        registry.register(p)
        registry.record_terminal(task_id="t1", state="stalled", evidence_refs=original)
        with pytest.raises(MissingEvidenceError):
            registry.register(
                _packet(
                    task_id="t2",
                    work_item_ref=p.work_item_ref,
                    stage=p.stage,
                    evidence_refs=original,
                )
            )

    def test_retry_with_genuinely_new_evidence_is_accepted(self):
        registry = TaskRegistry()
        original = (_ref("issue", "634"),)
        p = _packet(task_id="t1", evidence_refs=original)
        registry.register(p)
        registry.record_terminal(task_id="t1", state="failed", evidence_refs=original)
        registry.register(
            _packet(
                task_id="t2",
                work_item_ref=p.work_item_ref,
                stage=p.stage,
                evidence_refs=original + (_ref("pr", "999"),),
            )
        )

    def test_continuation_of_same_task_does_not_require_new_evidence(self):
        registry = TaskRegistry()
        p = _packet(task_id="t1", evidence_refs=(_ref("issue", "634"),))
        registry.register(p)
        registry.register(p)

    def test_evidence_ref_equality_is_value_based(self):
        # Identical-args EvidenceRef values are equal; retry with the same set is not new.
        assert _ref("issue", "634") == _ref("issue", "634")

    def test_opinion_does_not_count_as_new_evidence_for_retry(self):
        registry = TaskRegistry()
        original = (_ref("issue", "634"),)
        p = _packet(task_id="t1", evidence_refs=original)
        registry.register(p)
        registry.record_terminal(task_id="t1", state="failed", evidence_refs=original)
        retry = _packet(
            task_id="t2",
            work_item_ref=p.work_item_ref,
            stage=p.stage,
            evidence_refs=original,
            brief_opinion=BriefOpinion(likes="x", concerns="y", improvement="z"),
        )
        with pytest.raises(MissingEvidenceError):
            registry.register(retry)


# ===========================================================================
# 6. Deterministic stage transitions — complete lifecycle
# ===========================================================================


class TestStageTransitions:
    def test_contract_red_ready_transitions_to_contract_review(self):
        assert next_stage_after("contract", "RED_READY") == "contract-review"

    def test_contract_review_accepted_transitions_to_implementation(self):
        assert next_stage_after("contract-review", "ACCEPTED") == "implementation"

    def test_implementation_success_transitions_to_verification(self):
        assert next_stage_after("implementation", "success") == "verification"

    def test_verification_success_transitions_to_solution_review(self):
        assert next_stage_after("verification", "success") == "solution-review"

    def test_solution_review_accepted_transitions_to_maintain(self):
        assert next_stage_after("solution-review", "ACCEPTED") == "maintain"

    def test_maintain_ready_is_terminal(self):
        # No automatic forward stage past maintain; protected merge action takes over.
        assert next_stage_after("maintain", "READY") is None

    @pytest.mark.parametrize("outcome", ["BLOCKED", "failed", "stalled", "cancelled"])
    def test_blocking_outcomes_stop_all_transitions(self, outcome):
        for stage in CANONICAL_LIFECYCLE_STAGES:
            result = next_stage_after(stage, outcome)
            assert result is None, (
                f"next_stage_after({stage!r}, {outcome!r}) = {result!r}; expected None"
            )

    def test_full_forward_path_is_deterministic(self):
        forward = [
            ("contract", "RED_READY", "contract-review"),
            ("contract-review", "ACCEPTED", "implementation"),
            ("implementation", "success", "verification"),
            ("verification", "success", "solution-review"),
            ("solution-review", "ACCEPTED", "maintain"),
        ]
        for stage, outcome, expected in forward:
            result = next_stage_after(stage, outcome)
            assert result == expected, (
                f"next_stage_after({stage!r}, {outcome!r}) = {result!r}; expected {expected!r}"
            )

    def test_transition_result_is_canonical_or_none(self):
        outcomes = ("RED_READY", "ACCEPTED", "CHANGES_REQUESTED", "BLOCKED", "success", "failed")
        for stage in CANONICAL_LIFECYCLE_STAGES:
            for outcome in outcomes:
                result = next_stage_after(stage, outcome)
                assert result is None or result in CANONICAL_LIFECYCLE_STAGES

    def test_unknown_outcome_returns_none(self):
        assert next_stage_after("contract", "WHATS_THIS") is None


# ===========================================================================
# 7. Contract-review dispatch requires ACCEPTED evidence, not mere completion
# ===========================================================================


class TestContractReviewDispatch:
    def test_contract_review_accepted_dispatches_implementation(self):
        assert next_stage_after("contract-review", "ACCEPTED") == "implementation"

    def test_contract_task_completion_alone_does_not_dispatch_implementation(self):
        # "done"/"success" is a task-state outcome; without an explicit ACCEPTED
        # contract-review the lifecycle must stop, not jump to implementation.
        assert next_stage_after("contract", "done") != "implementation"
        assert next_stage_after("contract", "success") != "implementation"

    def test_implementation_rejected_while_contract_non_terminal(self):
        # Stage advancement requires prior stage material outcome.
        registry = TaskRegistry()
        registry.register(_packet(task_id="t1", work_item_ref="issues/1", stage="contract"))
        with pytest.raises(DuplicateActiveTaskError):
            registry.register(_impl_packet(task_id="t2", work_item_ref="issues/1"))

    def test_contract_review_accepted_allows_implementation_registration(self):
        # Full two-step dispatch: contract+RED_READY->contract-review,
        # then contract-review+ACCEPTED->implementation.
        registry = TaskRegistry()
        p_contract = _packet(task_id="t1", work_item_ref="issues/1", stage="contract")
        registry.register(p_contract)
        registry.record_terminal(
            task_id="t1", state="done", material_outcome="RED_READY",
            evidence_refs=(_ref("pr", "42"),),
        )
        p_cr = _packet(task_id="t2", work_item_ref="issues/1", stage="contract-review")
        registry.register(p_cr)
        registry.record_terminal(
            task_id="t2", state="done", material_outcome="ACCEPTED",
            evidence_refs=(_ref("pr", "43"),),
        )
        registry.register(_impl_packet(task_id="t3", work_item_ref="issues/1"))

    def test_contract_done_without_material_outcome_rejects_stage_advance(self):
        # A terminal "done" without a material_outcome cannot advance the lifecycle.
        registry = TaskRegistry()
        p = _packet(task_id="t1", work_item_ref="issues/1", stage="contract")
        registry.register(p)
        registry.record_terminal(
            task_id="t1", state="done", evidence_refs=(_ref("pr", "42"),)
        )
        with pytest.raises((DuplicateActiveTaskError, ValueError)):
            registry.register(
                _packet(task_id="t2", work_item_ref="issues/1", stage="contract-review")
            )


# ===========================================================================
# 8. Registry admission is wired to lifecycle transitions via material_outcome
# ===========================================================================


class TestRegistryLifecycleAdmission:
    def test_registry_rejects_stage_not_matching_next_stage_after(self):
        # Registering stage S2 when next_stage_after(S1, outcome) != S2 must fail.
        registry = TaskRegistry()
        registry.register(_packet(task_id="t1", work_item_ref="issues/5", stage="contract"))
        registry.record_terminal(
            task_id="t1", state="done", material_outcome="RED_READY",
            evidence_refs=(_ref("pr", "10"),),
        )
        # contract + RED_READY -> contract-review, NOT implementation
        with pytest.raises((DuplicateActiveTaskError, ValueError)):
            registry.register(_impl_packet(task_id="t2", work_item_ref="issues/5"))

    def test_registry_admits_stage_matching_next_stage_after(self):
        registry = TaskRegistry()
        registry.register(_packet(task_id="t1", work_item_ref="issues/5", stage="contract"))
        registry.record_terminal(
            task_id="t1", state="done", material_outcome="RED_READY",
            evidence_refs=(_ref("pr", "10"),),
        )
        # contract + RED_READY -> contract-review: valid
        registry.register(_packet(task_id="t2", work_item_ref="issues/5", stage="contract-review"))

    def test_failed_task_does_not_unlock_next_stage(self):
        # A failed terminal with no material_outcome must not advance the lifecycle.
        registry = TaskRegistry()
        registry.register(_packet(task_id="t1", work_item_ref="issues/6", stage="contract"))
        registry.record_terminal(
            task_id="t1", state="failed", evidence_refs=(_ref("pr", "10"),)
        )
        # next_stage_after("contract", None/"failed") == None, so contract-review is invalid
        with pytest.raises((DuplicateActiveTaskError, ValueError, MissingEvidenceError)):
            registry.register(
                _packet(task_id="t2", work_item_ref="issues/6", stage="contract-review")
            )

    def test_contract_review_red_ready_does_not_unlock_implementation(self):
        # contract-review + RED_READY is not a defined transition; only ACCEPTED unlocks implementation.
        assert next_stage_after("contract-review", "RED_READY") != "implementation"

    def test_implementation_success_does_not_unlock_solution_review_directly(self):
        # implementation + success -> verification, NOT solution-review directly.
        assert next_stage_after("implementation", "success") == "verification"
        assert next_stage_after("implementation", "success") != "solution-review"


# ===========================================================================
# 9. CHANGES_REQUESTED is a review outcome, not a canonical task state
# ===========================================================================


class TestChangesRequestedAsReviewOutcome:
    def test_changes_requested_from_solution_review_returns_to_implementation(self):
        assert next_stage_after("solution-review", "CHANGES_REQUESTED") == "implementation"

    def test_changes_requested_does_not_advance_to_maintain_or_loop_review(self):
        result = next_stage_after("solution-review", "CHANGES_REQUESTED")
        assert result not in ("maintain", "solution-review")

    def test_stale_solution_review_rejected_by_dispatch_model_api(self):
        # assert_solution_review_accepted_for_head from dispatch_model detects head mismatch.
        artifact = SolutionReviewArtifact(
            outcome="ACCEPTED",
            reviewed_head_sha="old-sha",
            reviewer_role="team-lead",
        )
        with pytest.raises(SolutionReviewRequiredError):
            assert_solution_review_accepted_for_head(artifact, current_head="new-sha")

    def test_changes_requested_review_outcome_rejects_merge(self):
        artifact = SolutionReviewArtifact(
            outcome="CHANGES_REQUESTED",
            reviewed_head_sha="impl-sha",
            reviewer_role="team-lead",
        )
        with pytest.raises(SolutionReviewRequiredError):
            assert_solution_review_accepted_for_head(artifact, current_head="impl-sha")

    def test_implementation_redispatch_after_changes_requested(self):
        # After CHANGES_REQUESTED: solution-review -> implementation -> verification -> solution-review.
        registry = TaskRegistry()
        work_item = "issues/1"

        # First implementation pass
        p_impl_v1 = _impl_packet(
            task_id="t-impl-v1", work_item_ref=work_item, current_head="impl-sha-v1"
        )
        registry.register(p_impl_v1)
        registry.record_terminal(
            task_id="t-impl-v1", state="done", material_outcome="success",
            evidence_refs=(_ref("pr", "10"),),
        )

        # Verification pass (required before solution-review)
        p_verify_v1 = _impl_packet(
            task_id="t-verify-v1", work_item_ref=work_item, stage="verification"
        )
        registry.register(p_verify_v1)
        registry.record_terminal(
            task_id="t-verify-v1", state="done", material_outcome="success",
            evidence_refs=(_ref("pr", "11"),),
        )

        # First solution-review: CHANGES_REQUESTED
        p_review = _impl_packet(
            task_id="t-review", work_item_ref=work_item,
            stage="solution-review", role="team-lead", current_head="impl-sha-v1",
        )
        registry.register(p_review)
        registry.record_terminal(
            task_id="t-review", state="done", material_outcome="CHANGES_REQUESTED",
            evidence_refs=(_ref("pr-review", "10"),),
        )

        # Re-implementation: valid (solution-review + CHANGES_REQUESTED -> implementation)
        p_impl_v2 = _impl_packet(
            task_id="t-impl-v2", work_item_ref=work_item,
            current_head="impl-sha-v2",
            evidence_refs=(
                _contract_provenance("contract-sha"),
                _ref("pr-review", "10"),
            ),
        )
        registry.register(p_impl_v2)

    def test_verification_required_before_second_solution_review(self):
        # After CHANGES_REQUESTED -> re-implementation, verification must complete
        # before another solution-review can be registered.
        registry = TaskRegistry()
        p_verify = _impl_packet(
            task_id="t-verify",
            work_item_ref="issues/1",
            stage="verification",
        )
        registry.register(p_verify)
        with pytest.raises(DuplicateActiveTaskError):
            registry.register(
                _impl_packet(
                    task_id="t-review2",
                    work_item_ref="issues/1",
                    stage="solution-review",
                    role="team-lead",
                )
            )


# ===========================================================================
# 10. BriefOpinion is advisory only — cannot alter state or transitions
# ===========================================================================


class TestBriefOpinionAdvisory:
    def test_opinion_does_not_change_next_stage(self):
        # next_stage_after accepts no opinion argument.
        assert next_stage_after("contract-review", "ACCEPTED") == "implementation"

    def test_packet_with_and_without_opinion_have_same_active_task_key(self):
        p_plain = _packet(work_item_ref="issues/1", stage="contract")
        opinion = BriefOpinion(likes="clean", concerns="scope", improvement="split")
        p_with = _packet(work_item_ref="issues/1", stage="contract", brief_opinion=opinion)
        assert p_plain.active_task_key == p_with.active_task_key

    def test_opinion_does_not_count_as_new_evidence_for_retry(self):
        registry = TaskRegistry()
        original = (_ref("issue", "634"),)
        p = _packet(task_id="t1", evidence_refs=original)
        registry.register(p)
        registry.record_terminal(task_id="t1", state="failed", evidence_refs=original)
        retry = _packet(
            task_id="t2",
            work_item_ref=p.work_item_ref,
            stage=p.stage,
            evidence_refs=original,
            brief_opinion=BriefOpinion(likes="x", concerns="y", improvement="z"),
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
# 11. Integration: orchestration uses the canonical task-state machine
# ===========================================================================


class TestCanonicalStateMachineIntegration:
    def test_terminal_task_outcomes_stop_forward_transitions(self):
        # Terminal state names from the canonical model must stop transitions.
        for outcome in ("done", "failed", "blocked", "cancelled", "stalled"):
            result = next_stage_after("contract", outcome)
            assert result is None, (
                f"next_stage_after('contract', {outcome!r}) = {result!r}; expected None"
            )

    def test_next_stage_after_handles_all_canonical_stages_without_raising(self):
        for stage in CANONICAL_LIFECYCLE_STAGES:
            next_stage_after(stage, "ACCEPTED")

    def test_stage_returned_is_always_canonical_or_none(self):
        outcomes = ("RED_READY", "ACCEPTED", "success", "CHANGES_REQUESTED")
        for stage in CANONICAL_LIFECYCLE_STAGES:
            for outcome in outcomes:
                result = next_stage_after(stage, outcome)
                assert result is None or result in CANONICAL_LIFECYCLE_STAGES, (
                    f"next_stage_after({stage!r}, {outcome!r}) = {result!r} is not canonical"
                )


# ===========================================================================
# 12. Full lifecycle integration: TaskRegistry wired to next_stage_after
# ===========================================================================


class TestLifecycleIntegration:
    def test_full_forward_lifecycle_drives_one_work_item_through_all_stages(self):
        """
        Proves TaskRegistry and next_stage_after cannot be implemented as
        unrelated components: each stage registration is gate-kept by the
        previous stage's material_outcome via next_stage_after.
        """
        registry = TaskRegistry()
        work_item = "issues/634"

        # contract -> contract-review
        p_contract = _packet(task_id="t-contract", work_item_ref=work_item, stage="contract")
        registry.register(p_contract)
        assert next_stage_after("contract", "RED_READY") == "contract-review"
        registry.record_terminal(
            task_id="t-contract", state="done", material_outcome="RED_READY",
            evidence_refs=(_ref("pr", "1"),),
        )

        # contract-review -> implementation
        p_cr = _packet(task_id="t-cr", work_item_ref=work_item, stage="contract-review")
        registry.register(p_cr)
        assert next_stage_after("contract-review", "ACCEPTED") == "implementation"
        registry.record_terminal(
            task_id="t-cr", state="done", material_outcome="ACCEPTED",
            evidence_refs=(_ref("pr", "2"),),
        )

        # implementation -> verification
        p_impl = _impl_packet(task_id="t-impl", work_item_ref=work_item)
        registry.register(p_impl)
        assert next_stage_after("implementation", "success") == "verification"
        registry.record_terminal(
            task_id="t-impl", state="done", material_outcome="success",
            evidence_refs=(_ref("pr", "3"),),
        )

        # verification -> solution-review
        p_verify = _impl_packet(task_id="t-verify", work_item_ref=work_item, stage="verification")
        registry.register(p_verify)
        assert next_stage_after("verification", "success") == "solution-review"
        registry.record_terminal(
            task_id="t-verify", state="done", material_outcome="success",
            evidence_refs=(_ref("pr", "4"),),
        )

        # solution-review -> maintain
        p_review = _impl_packet(
            task_id="t-review", work_item_ref=work_item,
            stage="solution-review", role="team-lead",
        )
        registry.register(p_review)
        assert next_stage_after("solution-review", "ACCEPTED") == "maintain"
        registry.record_terminal(
            task_id="t-review", state="done", material_outcome="ACCEPTED",
            evidence_refs=(_ref("pr", "5"),),
        )

        # maintain -> None (protected merge action takes over)
        p_maintain = _impl_packet(task_id="t-maintain", work_item_ref=work_item, stage="maintain")
        registry.register(p_maintain)
        assert next_stage_after("maintain", "READY") is None

    def test_changes_requested_path_requires_verification_before_second_solution_review(self):
        """After CHANGES_REQUESTED, path must be implementation -> verification -> solution-review."""
        registry = TaskRegistry()
        work_item = "issues/635"

        # First implementation pass
        p_impl_v1 = _impl_packet(task_id="t-impl-v1", work_item_ref=work_item)
        registry.register(p_impl_v1)
        registry.record_terminal(
            task_id="t-impl-v1", state="done", material_outcome="success",
            evidence_refs=(_ref("pr", "10"),),
        )

        # Verification required before first solution-review
        p_verify_v1 = _impl_packet(
            task_id="t-verify-v1", work_item_ref=work_item, stage="verification"
        )
        registry.register(p_verify_v1)
        registry.record_terminal(
            task_id="t-verify-v1", state="done", material_outcome="success",
            evidence_refs=(_ref("pr", "11"),),
        )

        # First solution-review returns CHANGES_REQUESTED
        p_review_v1 = _impl_packet(
            task_id="t-review-v1", work_item_ref=work_item,
            stage="solution-review", role="team-lead",
        )
        registry.register(p_review_v1)
        assert next_stage_after("solution-review", "CHANGES_REQUESTED") == "implementation"
        registry.record_terminal(
            task_id="t-review-v1", state="done", material_outcome="CHANGES_REQUESTED",
            evidence_refs=(_ref("pr-review", "10"),),
        )

        # Re-implementation: valid (next_stage_after == "implementation")
        p_impl_v2 = _impl_packet(
            task_id="t-impl-v2", work_item_ref=work_item, current_head="impl-sha-v2",
            evidence_refs=(_contract_provenance("contract-sha"), _ref("pr-review", "10")),
        )
        registry.register(p_impl_v2)
        registry.record_terminal(
            task_id="t-impl-v2", state="done", material_outcome="success",
            evidence_refs=(_ref("pr", "20"),),
        )

        # Attempting solution-review without verification must fail
        with pytest.raises((DuplicateActiveTaskError, ValueError)):
            registry.register(
                _impl_packet(
                    task_id="t-review-v2-bad", work_item_ref=work_item,
                    stage="solution-review", role="team-lead",
                )
            )

        # Verification completes: solution-review is now valid
        p_verify_v2 = _impl_packet(
            task_id="t-verify-v2", work_item_ref=work_item, stage="verification"
        )
        registry.register(p_verify_v2)
        registry.record_terminal(
            task_id="t-verify-v2", state="done", material_outcome="success",
            evidence_refs=(_ref("pr", "21"),),
        )
        registry.register(
            _impl_packet(
                task_id="t-review-v2", work_item_ref=work_item,
                stage="solution-review", role="team-lead",
            )
        )
