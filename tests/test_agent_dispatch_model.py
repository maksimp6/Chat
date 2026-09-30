import pytest

from agent_office.dispatch_model import (
    AgentDispatchError,
    AgentTaskPlan,
    CANONICAL_LIFECYCLE_STAGES,
    COORDINATION_ONLY_ROLES,
    IMPLEMENTATION_ROLES,
    REVIEW_ONLY_BACKENDS,
    resolve_task_plan,
)


def test_lifecycle_stages_are_not_agent_roles():
    assert CANONICAL_LIFECYCLE_STAGES == (
        "contract",
        "contract-review",
        "implementation",
        "verification",
        "solution-review",
        "maintain",
    )
    assert "implementation" not in IMPLEMENTATION_ROLES
    assert "agent-implement" not in IMPLEMENTATION_ROLES


def test_implementation_requires_a_concrete_engineering_role():
    with pytest.raises(AgentDispatchError, match="concrete implementation role"):
        resolve_task_plan(stage="implementation", role=None, backend="claude-direct")

    plan = resolve_task_plan(
        stage="implementation",
        role="backend-engineer",
        backend="claude-direct",
    )
    assert isinstance(plan, AgentTaskPlan)
    assert plan.stage == "implementation"
    assert plan.role == "backend-engineer"
    assert plan.backend == "claude-direct"


def test_implementation_rejects_supervisory_and_review_roles():
    for role in (
        "team-lead",
        "work-coordinator",
        "test-engineer",
        "security-reviewer",
        "release-manager",
        "operations-observer",
        "process-governor",
    ):
        with pytest.raises(AgentDispatchError, match="not an implementation role"):
            resolve_task_plan(
                stage="implementation",
                role=role,
                backend="claude-direct",
            )


def test_work_coordinator_is_coordination_only_and_cannot_review_solution():
    assert "work-coordinator" in COORDINATION_ONLY_ROLES
    plan = resolve_task_plan(stage="verification", role="work-coordinator")
    assert plan.can_implement is False
    assert plan.can_merge is False
    assert plan.can_review_solution is False

    with pytest.raises(AgentDispatchError, match="coordination-only"):
        resolve_task_plan(stage="solution-review", role="work-coordinator")


def test_current_default_backend_for_test_and_implementation_is_claude_direct():
    test_plan = resolve_task_plan(stage="contract", role="test-engineer")
    impl_plan = resolve_task_plan(stage="implementation", role="infra-engineer")

    assert test_plan.backend == "claude-direct"
    assert impl_plan.backend == "claude-direct"


def test_copilot_is_review_only_not_implementation_backend():
    assert "copilot" in REVIEW_ONLY_BACKENDS

    with pytest.raises(AgentDispatchError, match="review-only backend"):
        resolve_task_plan(
            stage="implementation",
            role="backend-engineer",
            backend="copilot",
        )


def test_backend_does_not_change_role_or_authority():
    direct = resolve_task_plan(
        stage="implementation",
        role="backend-engineer",
        backend="claude-direct",
    )
    codex = resolve_task_plan(
        stage="implementation",
        role="backend-engineer",
        backend="codex",
    )

    assert direct.role == codex.role == "backend-engineer"
    assert direct.can_merge is codex.can_merge is False
    assert direct.can_bypass_approval is codex.can_bypass_approval is False


def test_test_engineer_can_author_contract_but_not_implement():
    plan = resolve_task_plan(stage="contract", role="test-engineer")

    assert plan.can_write_contract_tests is True
    assert plan.can_implement is False
    assert plan.can_merge is False


def test_contract_review_is_team_lead_stage_not_implementation():
    plan = resolve_task_plan(stage="contract-review", role="team-lead")

    assert plan.can_review_contract is True
    assert plan.can_implement is False
    assert plan.can_merge is False


def test_release_manager_merge_never_bypasses_approval():
    plan = resolve_task_plan(stage="maintain", role="release-manager")

    assert plan.can_merge is True
    assert plan.can_bypass_approval is False


def test_unknown_stage_role_or_backend_fails_closed():
    with pytest.raises(AgentDispatchError, match="unknown lifecycle stage"):
        resolve_task_plan(stage="magic", role="backend-engineer")
    with pytest.raises(AgentDispatchError, match="unknown role"):
        resolve_task_plan(stage="implementation", role="wizard")
    with pytest.raises(AgentDispatchError, match="unknown backend"):
        resolve_task_plan(
            stage="implementation",
            role="backend-engineer",
            backend="mystery-ai",
        )
