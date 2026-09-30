"""RED contract tests for the solution-review lifecycle stage (issue #621).

These tests define the behavioral contract for the mandatory independent
solution-quality review stage required between verification and maintain/merge.

ALL tests are expected to FAIL (RED) until the production implementation is added.
Do not add implementation code here.
"""
import pytest

from agent_office.dispatch_model import (
    AgentDispatchError,
    CANONICAL_LIFECYCLE_STAGES,
    IMPLEMENTATION_ROLES,
    resolve_task_plan,
)


# ---------------------------------------------------------------------------
# 1. Lifecycle stage presence and ordering
# ---------------------------------------------------------------------------


def test_solution_review_stage_exists_in_canonical_lifecycle():
    """solution-review must be a registered lifecycle stage."""
    assert "solution-review" in CANONICAL_LIFECYCLE_STAGES


def test_solution_review_stage_is_between_verification_and_maintain():
    """Stage order must be: verification → solution-review → maintain."""
    stages = list(CANONICAL_LIFECYCLE_STAGES)
    assert "solution-review" in stages
    assert "verification" in stages
    assert "maintain" in stages
    idx_sr = stages.index("solution-review")
    assert stages.index("verification") == idx_sr - 1
    assert stages.index("maintain") == idx_sr + 1


def test_solution_review_stage_is_recognized_by_resolve_task_plan():
    """resolve_task_plan must not raise AgentDispatchError for solution-review stage."""
    plan = resolve_task_plan(stage="solution-review", role="team-lead", backend="claude-direct")
    assert plan.stage == "solution-review"


# ---------------------------------------------------------------------------
# 2. Capability flag: can_review_solution
# ---------------------------------------------------------------------------


def test_agent_task_plan_has_can_review_solution_flag():
    """AgentTaskPlan must expose a can_review_solution capability flag."""
    plan = resolve_task_plan(stage="solution-review", role="team-lead")
    assert hasattr(plan, "can_review_solution")


def test_can_review_solution_true_in_solution_review_stage():
    """can_review_solution must be True for the solution-review stage with a valid reviewer role."""
    plan = resolve_task_plan(stage="solution-review", role="team-lead")
    assert plan.can_review_solution is True


def test_can_review_solution_false_in_all_other_stages():
    """can_review_solution must be False in every stage other than solution-review."""
    stage_role_pairs = [
        ("contract", "test-engineer"),
        ("contract-review", "team-lead"),
        ("implementation", "backend-engineer"),
        ("verification", "test-engineer"),
        ("maintain", "release-manager"),
    ]
    for stage, role in stage_role_pairs:
        plan = resolve_task_plan(stage=stage, role=role)
        assert plan.can_review_solution is False, (
            f"can_review_solution must be False at stage={stage!r}, got True"
        )


# ---------------------------------------------------------------------------
# 3. Independence: implementation roles cannot perform solution review
# ---------------------------------------------------------------------------


def test_implementation_roles_cannot_perform_solution_review():
    """An implementation role must be rejected at the solution-review stage."""
    for role in IMPLEMENTATION_ROLES:
        with pytest.raises(AgentDispatchError, match="implementation role"):
            resolve_task_plan(stage="solution-review", role=role)


def test_solution_review_allows_non_implementation_supervisory_roles():
    """Non-implementation supervisory roles must be permitted as solution reviewer."""
    for role in ("team-lead", "security-reviewer", "process-governor"):
        plan = resolve_task_plan(stage="solution-review", role=role, backend="claude-direct")
        assert plan.can_review_solution is True, (
            f"expected can_review_solution True for role={role!r}"
        )


def test_solution_review_role_must_differ_from_implementation_stage_role():
    """The same role cannot author implementation and then perform solution review."""
    for impl_role in IMPLEMENTATION_ROLES:
        impl_plan = resolve_task_plan(
            stage="implementation",
            role=impl_role,
            backend="claude-direct",
        )
        assert impl_plan.can_implement is True

        # That same role must be rejected at solution-review
        with pytest.raises(AgentDispatchError):
            resolve_task_plan(stage="solution-review", role=impl_role, backend="claude-direct")


# ---------------------------------------------------------------------------
# 4. SolutionReviewArtifact — importable, exact-head-specific
# ---------------------------------------------------------------------------


def test_solution_review_artifact_is_importable():
    """SolutionReviewArtifact must be importable from dispatch_model."""
    from agent_office.dispatch_model import SolutionReviewArtifact  # noqa: F401


def test_valid_review_outcomes_are_importable():
    """VALID_REVIEW_OUTCOMES must be importable and contain the three material outcomes."""
    from agent_office.dispatch_model import VALID_REVIEW_OUTCOMES

    assert "ACCEPTED" in VALID_REVIEW_OUTCOMES
    assert "CHANGES_REQUESTED" in VALID_REVIEW_OUTCOMES
    assert "BLOCKED" in VALID_REVIEW_OUTCOMES


def test_solution_review_artifact_accepted_construction():
    """SolutionReviewArtifact must be constructable with required fields."""
    from agent_office.dispatch_model import SolutionReviewArtifact

    artifact = SolutionReviewArtifact(
        outcome="ACCEPTED",
        reviewed_head_sha="abc1234deadbeef",
        reviewer_role="team-lead",
    )
    assert artifact.outcome == "ACCEPTED"
    assert artifact.reviewed_head_sha == "abc1234deadbeef"
    assert artifact.reviewer_role == "team-lead"


def test_solution_review_artifact_rejects_invalid_outcome():
    """SolutionReviewArtifact must raise InvalidReviewOutcome for unrecognised outcomes."""
    from agent_office.dispatch_model import SolutionReviewArtifact, InvalidReviewOutcome

    with pytest.raises(InvalidReviewOutcome):
        SolutionReviewArtifact(
            outcome="LGTM",
            reviewed_head_sha="abc",
            reviewer_role="team-lead",
        )

    with pytest.raises(InvalidReviewOutcome):
        SolutionReviewArtifact(
            outcome="",
            reviewed_head_sha="abc",
            reviewer_role="team-lead",
        )


# ---------------------------------------------------------------------------
# 5. assert_solution_review_accepted_for_head — merge gate function
# ---------------------------------------------------------------------------


def test_assert_solution_review_accepted_for_head_is_importable():
    """assert_solution_review_accepted_for_head and SolutionReviewRequiredError must be importable."""
    from agent_office.dispatch_model import (  # noqa: F401
        assert_solution_review_accepted_for_head,
        SolutionReviewRequiredError,
    )


def test_merge_gate_passes_for_accepted_artifact_with_matching_sha():
    """Must not raise when artifact is ACCEPTED and SHA matches current head."""
    from agent_office.dispatch_model import (
        SolutionReviewArtifact,
        assert_solution_review_accepted_for_head,
    )

    artifact = SolutionReviewArtifact(
        outcome="ACCEPTED",
        reviewed_head_sha="exact123abc",
        reviewer_role="team-lead",
    )
    assert_solution_review_accepted_for_head(artifact, current_head="exact123abc")


def test_merge_gate_fails_for_accepted_artifact_with_wrong_sha():
    """Must raise SolutionReviewRequiredError when SHA does not match, even if ACCEPTED."""
    from agent_office.dispatch_model import (
        SolutionReviewArtifact,
        assert_solution_review_accepted_for_head,
        SolutionReviewRequiredError,
    )

    artifact = SolutionReviewArtifact(
        outcome="ACCEPTED",
        reviewed_head_sha="stale_sha",
        reviewer_role="team-lead",
    )
    with pytest.raises(SolutionReviewRequiredError, match="head SHA"):
        assert_solution_review_accepted_for_head(artifact, current_head="new_sha")


def test_merge_gate_fails_for_changes_requested_artifact():
    """CHANGES_REQUESTED outcome must not satisfy the merge gate even for matching SHA."""
    from agent_office.dispatch_model import (
        SolutionReviewArtifact,
        assert_solution_review_accepted_for_head,
        SolutionReviewRequiredError,
    )

    artifact = SolutionReviewArtifact(
        outcome="CHANGES_REQUESTED",
        reviewed_head_sha="head123",
        reviewer_role="team-lead",
    )
    with pytest.raises(SolutionReviewRequiredError, match="CHANGES_REQUESTED"):
        assert_solution_review_accepted_for_head(artifact, current_head="head123")


def test_merge_gate_fails_for_blocked_artifact():
    """BLOCKED outcome must not satisfy the merge gate."""
    from agent_office.dispatch_model import (
        SolutionReviewArtifact,
        assert_solution_review_accepted_for_head,
        SolutionReviewRequiredError,
    )

    artifact = SolutionReviewArtifact(
        outcome="BLOCKED",
        reviewed_head_sha="head123",
        reviewer_role="team-lead",
    )
    with pytest.raises(SolutionReviewRequiredError, match="BLOCKED"):
        assert_solution_review_accepted_for_head(artifact, current_head="head123")


def test_merge_gate_fails_when_no_artifact_exists():
    """Absence of any artifact (None) must fail closed — no review was performed."""
    from agent_office.dispatch_model import (
        assert_solution_review_accepted_for_head,
        SolutionReviewRequiredError,
    )

    with pytest.raises(SolutionReviewRequiredError, match="no solution review"):
        assert_solution_review_accepted_for_head(None, current_head="abc123")


# ---------------------------------------------------------------------------
# 6. CHANGES_REQUESTED forces re-verification before next solution review
# ---------------------------------------------------------------------------


def test_changes_requested_requires_returning_to_implementation_stage():
    """After CHANGES_REQUESTED the lifecycle must return to implementation, not solution-review."""
    # The contract: an implementation role must be valid at the implementation stage
    # (the role exists after CHANGES_REQUESTED sends work back).
    impl_plan = resolve_task_plan(
        stage="implementation",
        role="backend-engineer",
        backend="claude-direct",
    )
    assert impl_plan.can_implement is True

    # And verification must precede a new solution-review attempt.
    # Verification stage must not grant solution review capability.
    verification_plan = resolve_task_plan(stage="verification", role="test-engineer")
    assert verification_plan.can_review_solution is False


def test_new_solution_review_artifact_required_after_changes_requested():
    """A CHANGES_REQUESTED artifact at an old SHA cannot satisfy a later head."""
    from agent_office.dispatch_model import (
        SolutionReviewArtifact,
        assert_solution_review_accepted_for_head,
        SolutionReviewRequiredError,
    )

    # Round 1: review returned CHANGES_REQUESTED
    round1_artifact = SolutionReviewArtifact(
        outcome="CHANGES_REQUESTED",
        reviewed_head_sha="sha_before_fixes",
        reviewer_role="team-lead",
    )

    # After fixes a new commit was pushed (new SHA)
    new_head = "sha_after_fixes"

    # The old CHANGES_REQUESTED artifact must not satisfy the gate for the new head
    with pytest.raises(SolutionReviewRequiredError):
        assert_solution_review_accepted_for_head(round1_artifact, current_head=new_head)


# ---------------------------------------------------------------------------
# 7. Release Manager: fail-closed without accepted exact-head solution review
# ---------------------------------------------------------------------------


def test_release_manager_has_can_merge_true_at_maintain_stage():
    """Release Manager at maintain stage still has can_merge True (unchanged)."""
    plan = resolve_task_plan(stage="maintain", role="release-manager")
    assert plan.can_merge is True
    assert plan.can_bypass_approval is False


def test_release_manager_cannot_merge_without_any_solution_review():
    """Release Manager must fail closed when no solution review artifact exists."""
    from agent_office.dispatch_model import (
        assert_solution_review_accepted_for_head,
        SolutionReviewRequiredError,
    )

    with pytest.raises(SolutionReviewRequiredError, match="no solution review"):
        assert_solution_review_accepted_for_head(None, current_head="production_head")


def test_release_manager_merge_gate_is_separate_from_can_bypass_approval():
    """Solution review gate is independent of can_bypass_approval (both enforced)."""
    from agent_office.dispatch_model import (
        assert_solution_review_accepted_for_head,
        SolutionReviewRequiredError,
    )

    plan = resolve_task_plan(stage="maintain", role="release-manager")

    # can_bypass_approval is always False
    assert plan.can_bypass_approval is False

    # Solution review is a separate gate; no artifact → must fail closed
    with pytest.raises(SolutionReviewRequiredError):
        assert_solution_review_accepted_for_head(None, current_head="any_sha")


# ---------------------------------------------------------------------------
# 8. Backend independence: same backend allowed; distinct role+stage required
# ---------------------------------------------------------------------------


def test_solution_review_allows_same_claude_direct_backend_as_implementation():
    """Same backend (claude-direct) is permitted; role and stage must differ."""
    impl_plan = resolve_task_plan(
        stage="implementation",
        role="backend-engineer",
        backend="claude-direct",
    )
    review_plan = resolve_task_plan(
        stage="solution-review",
        role="team-lead",
        backend="claude-direct",
    )

    assert impl_plan.backend == review_plan.backend == "claude-direct"
    assert impl_plan.role != review_plan.role
    assert impl_plan.stage != review_plan.stage


# ---------------------------------------------------------------------------
# 9. GREEN verification alone is insufficient for merge
# ---------------------------------------------------------------------------


def test_verification_stage_does_not_satisfy_solution_review():
    """Passing the verification stage must not grant solution review capability."""
    plan = resolve_task_plan(stage="verification", role="test-engineer")
    assert plan.can_review_solution is False


def test_green_ci_without_artifact_fails_merge_gate():
    """GREEN CI (verification stage passed) without an accepted artifact must fail closed."""
    from agent_office.dispatch_model import (
        assert_solution_review_accepted_for_head,
        SolutionReviewRequiredError,
    )

    # CI is green — verification stage completed without error
    ci_green = True  # noqa: F841

    # But no solution review artifact exists
    with pytest.raises(SolutionReviewRequiredError, match="no solution review"):
        assert_solution_review_accepted_for_head(None, current_head="green_head_sha")
