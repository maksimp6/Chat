"""Role-stage-backend dispatch model for agent_office."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

CANONICAL_LIFECYCLE_STAGES = (
    "contract",
    "contract-review",
    "implementation",
    "verification",
    "solution-review",
    "maintain",
)

IMPLEMENTATION_ROLES = frozenset(
    {
        "backend-engineer",
        "frontend-engineer",
        "android-engineer",
        "infra-engineer",
        "docs-engineer",
    }
)

REVIEW_ONLY_BACKENDS = frozenset({"copilot"})

_ALL_ROLES = frozenset(
    {
        "team-lead",
        "backend-engineer",
        "frontend-engineer",
        "android-engineer",
        "test-engineer",
        "infra-engineer",
        "security-reviewer",
        "docs-engineer",
        "release-manager",
        "operations-observer",
        "process-governor",
    }
)

_ALL_BACKENDS = frozenset(
    {
        "codex",
        "copilot",
        "github-actions",
    }
)

_DEFAULT_BACKEND = "codex"


class AgentDispatchError(Exception):
    pass


class InvalidReviewOutcome(Exception):
    pass


class SolutionReviewRequiredError(Exception):
    pass


VALID_REVIEW_OUTCOMES = frozenset({"ACCEPTED", "CHANGES_REQUESTED", "BLOCKED"})


@dataclass
class SolutionReviewArtifact:
    outcome: str
    reviewed_head_sha: str
    reviewer_role: str

    def __post_init__(self) -> None:
        if self.outcome not in VALID_REVIEW_OUTCOMES:
            raise InvalidReviewOutcome(
                f"outcome {self.outcome!r} is not valid; must be one of {sorted(VALID_REVIEW_OUTCOMES)}"
            )


def assert_solution_review_accepted_for_head(
    artifact: Optional[SolutionReviewArtifact],
    current_head: str,
) -> None:
    if artifact is None:
        raise SolutionReviewRequiredError(
            "no solution review artifact found; solution review is required before merge"
        )
    if artifact.outcome != "ACCEPTED":
        raise SolutionReviewRequiredError(
            f"solution review outcome is {artifact.outcome}; merge is not permitted"
        )
    if artifact.reviewed_head_sha != current_head:
        raise SolutionReviewRequiredError(
            f"solution review was for head SHA {artifact.reviewed_head_sha!r},"
            f" but current head SHA is {current_head!r}; a new review is required"
        )


@dataclass
class AgentTaskPlan:
    stage: str
    role: Optional[str]
    backend: str
    can_merge: bool = False
    can_bypass_approval: bool = False
    can_implement: bool = False
    can_write_contract_tests: bool = False
    can_review_contract: bool = False
    can_review_solution: bool = False


def resolve_task_plan(
    stage: str,
    role: Optional[str] = None,
    backend: Optional[str] = None,
) -> AgentTaskPlan:
    if stage not in CANONICAL_LIFECYCLE_STAGES:
        raise AgentDispatchError(f"unknown lifecycle stage: {stage!r}")

    if role is not None and role not in _ALL_ROLES:
        raise AgentDispatchError(f"unknown role: {role!r}")

    effective_backend = backend if backend is not None else _DEFAULT_BACKEND

    if effective_backend not in _ALL_BACKENDS:
        raise AgentDispatchError(f"unknown backend: {effective_backend!r}")

    if stage == "implementation":
        if role is None:
            raise AgentDispatchError("implementation stage requires a concrete implementation role")
        if role not in IMPLEMENTATION_ROLES:
            raise AgentDispatchError(f"{role!r} is not an implementation role")
        if effective_backend in REVIEW_ONLY_BACKENDS:
            raise AgentDispatchError(
                f"{effective_backend!r} is a review-only backend"
                " and cannot be used for implementation"
            )

    if stage == "solution-review":
        if role in IMPLEMENTATION_ROLES:
            raise AgentDispatchError(
                f"{role!r} is an implementation role and cannot perform solution review;"
                " use a non-implementation supervisory role"
            )

    can_merge = stage == "maintain" and role == "release-manager"
    can_implement = stage == "implementation" and role in IMPLEMENTATION_ROLES
    can_write_contract_tests = stage == "contract" and role == "test-engineer"
    can_review_contract = stage == "contract-review" and role == "team-lead"
    can_review_solution = (
        stage == "solution-review" and role is not None and role not in IMPLEMENTATION_ROLES
    )

    return AgentTaskPlan(
        stage=stage,
        role=role,
        backend=effective_backend,
        can_merge=can_merge,
        can_bypass_approval=False,
        can_implement=can_implement,
        can_write_contract_tests=can_write_contract_tests,
        can_review_contract=can_review_contract,
        can_review_solution=can_review_solution,
    )
