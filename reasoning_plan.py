"""Structured planning contract for Alice Pro reasoning workflows.

This module models planning state, validation and replanning only.
It does not expose or persist a model private chain-of-thought.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Iterable


class PlanType(StrEnum):
    ANALYSIS = "analysis"
    IMPLEMENTATION = "implementation"
    DEBUG = "debug"
    RESEARCH = "research"
    MIGRATION = "migration"
    DEPLOYMENT = "deployment"
    MAINTENANCE = "maintenance"


class PlanStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    RUNNING = "running"
    BLOCKED = "blocked"
    REPLANNING = "replanning"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(slots=True)
class PlanStep:
    id: str
    action: str
    target: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"
    success_criteria: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ReasoningPlan:
    goal: str
    plan_type: PlanType
    success_criteria: list[str]
    steps: list[PlanStep]
    plan_id: str | None = None
    status: PlanStatus = PlanStatus.DRAFT
    context: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    replan_count: int = 0
    blocked_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["plan_type"] = self.plan_type.value
        value["status"] = self.status.value
        return value


@dataclass(frozen=True, slots=True)
class PlanValidation:
    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "errors": list(self.errors), "warnings": list(self.warnings)}


class PlanValidator:
    """Validate plan structure before an executor can run it."""

    def validate(self, plan: ReasoningPlan) -> PlanValidation:
        errors: list[str] = []
        warnings: list[str] = []

        if not plan.goal.strip():
            errors.append("goal is required")
        if not plan.success_criteria:
            errors.append("at least one success criterion is required")
        if not plan.steps:
            errors.append("at least one plan step is required")

        step_ids = [step.id for step in plan.steps]
        if any(not step_id.strip() for step_id in step_ids):
            errors.append("step ids must not be empty")
        duplicates = sorted({step_id for step_id in step_ids if step_ids.count(step_id) > 1})
        if duplicates:
            errors.append(f"duplicate step ids: {", ".join(duplicates)}")

        known = set(step_ids)
        for step in plan.steps:
            missing = sorted(set(step.depends_on) - known)
            if missing:
                errors.append(f"step {step.id!r} depends on unknown steps: {", ".join(missing)}")
            if step.id in step.depends_on:
                errors.append(f"step {step.id!r} cannot depend on itself")

        if not errors:
            cycle = self._find_cycle(plan.steps)
            if cycle:
                errors.append(f"dependency cycle detected: {" -> ".join(cycle)}")

        if not plan.context:
            warnings.append("plan has no collected context")
        if any(not step.success_criteria for step in plan.steps):
            warnings.append("some steps have no step-level success criteria")

        return PlanValidation(not errors, tuple(errors), tuple(warnings))

    @staticmethod
    def _find_cycle(steps: Iterable[PlanStep]) -> list[str] | None:
        graph = {step.id: step.depends_on for step in steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str, path: list[str]) -> list[str] | None:
            if node in visiting:
                return path[path.index(node):] + [node]
            if node in visited:
                return None
            visiting.add(node)
            for dependency in graph.get(node, []):
                cycle = visit(dependency, path + [dependency])
                if cycle:
                    return cycle
            visiting.remove(node)
            visited.add(node)
            return None

        for node in graph:
            cycle = visit(node, [node])
            if cycle:
                return cycle
        return None


class ReasoningPlanner:
    """Lifecycle coordinator for explicit structured plans."""

    def __init__(self, validator: PlanValidator | None = None) -> None:
        self.validator = validator or PlanValidator()

    def validate(self, plan: ReasoningPlan) -> PlanValidation:
        result = self.validator.validate(plan)
        if result.valid:
            plan.status = PlanStatus.VALIDATED
            plan.blocked_reason = None
        else:
            plan.status = PlanStatus.BLOCKED
            plan.blocked_reason = "; ".join(result.errors)
        return result

    def start(self, plan: ReasoningPlan) -> PlanValidation:
        result = self.validate(plan)
        if result.valid:
            plan.status = PlanStatus.RUNNING
        return result

    def replan(self, plan: ReasoningPlan, *, reason: str, steps: list[PlanStep] | None = None,
               success_criteria: list[str] | None = None) -> PlanValidation:
        plan.replan_count += 1
        plan.status = PlanStatus.REPLANNING
        plan.blocked_reason = reason
        if steps is not None:
            plan.steps = steps
        if success_criteria is not None:
            plan.success_criteria = success_criteria
        return self.validate(plan)

    def complete(self, plan: ReasoningPlan, *, criteria_met: bool) -> None:
        plan.status = PlanStatus.COMPLETED if criteria_met else PlanStatus.FAILED
        if criteria_met:
            plan.blocked_reason = None
