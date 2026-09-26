from reasoning_plan import (
    PlanStatus,
    PlanStep,
    PlanType,
    PlanValidator,
    ReasoningPlan,
    ReasoningPlanner,
)


def make_plan(**overrides):
    values = {
        "goal": "Implement a feature",
        "plan_type": PlanType.IMPLEMENTATION,
        "success_criteria": ["tests pass"],
        "context": {"files": ["app.py"]},
        "steps": [
            PlanStep(
                id="inspect",
                action="inspect",
                target=["app.py"],
                success_criteria=["relevant code identified"],
            ),
            PlanStep(
                id="implement",
                action="modify",
                target=["app.py"],
                depends_on=["inspect"],
                success_criteria=["change is implemented"],
            ),
            PlanStep(
                id="verify",
                action="test",
                depends_on=["implement"],
                success_criteria=["tests pass"],
            ),
        ],
    }
    values.update(overrides)
    return ReasoningPlan(**values)


def test_valid_plan_is_accepted_and_started():
    plan = make_plan()
    result = ReasoningPlanner().start(plan)
    assert result.valid is True
    assert plan.status == PlanStatus.RUNNING


def test_unknown_dependency_is_rejected():
    plan = make_plan(steps=[PlanStep(id="implement", action="modify", depends_on=["missing"])])
    result = PlanValidator().validate(plan)
    assert result.valid is False
    assert "unknown steps" in result.errors[0]


def test_dependency_cycle_is_rejected():
    plan = make_plan(
        steps=[
            PlanStep(id="a", action="inspect", depends_on=["b"]),
            PlanStep(id="b", action="modify", depends_on=["a"]),
        ]
    )
    result = PlanValidator().validate(plan)
    assert result.valid is False
    assert any("dependency cycle" in error for error in result.errors)


def test_missing_goal_and_success_criteria_are_rejected():
    plan = make_plan(goal=" ", success_criteria=[])
    result = PlanValidator().validate(plan)
    assert result.valid is False
    assert "goal is required" in result.errors
    assert "at least one success criterion is required" in result.errors


def test_replan_increments_counter_and_revalidates():
    plan = make_plan()
    planner = ReasoningPlanner()
    planner.start(plan)
    result = planner.replan(
        plan,
        reason="test failure",
        steps=[
            PlanStep(
                id="fix", action="modify", target=["app.py"], success_criteria=["regression fixed"]
            ),
            PlanStep(
                id="verify", action="test", depends_on=["fix"], success_criteria=["tests pass"]
            ),
        ],
    )
    assert result.valid is True
    assert plan.replan_count == 1
    assert plan.status == PlanStatus.VALIDATED
    assert plan.blocked_reason is None


def test_complete_requires_explicit_success_result():
    plan = make_plan()
    planner = ReasoningPlanner()
    planner.complete(plan, criteria_met=False)
    assert plan.status == PlanStatus.FAILED
    planner.complete(plan, criteria_met=True)
    assert plan.status == PlanStatus.COMPLETED


def test_plan_serialization_is_model_friendly():
    payload = make_plan().to_dict()
    assert payload["plan_type"] == "implementation"
    assert payload["status"] == "draft"
    assert payload["steps"][1]["depends_on"] == ["inspect"]
