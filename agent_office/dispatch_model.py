"""Role-stage-backend dispatch model for agent_office."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

CANONICAL_LIFECYCLE_STAGES = (
    "contract",
    "contract-review",
    "implementation",
    "verification",
    "review",
    "maintain",
)

# Concrete engineering roles that may perform implementation work.
# Lifecycle stage names ("implementation") are never placed here.
IMPLEMENTATION_ROLES = frozenset(
    {
        "backend-engineer",
        "frontend-engineer",
        "android-engineer",
        "infra-engineer",
        "docs-engineer",
    }
)

# Backends that are review-only; rejected for implementation stages.
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
        "claude-direct",
        "codex",
        "copilot",
        "github-actions",
    }
)

_DEFAULT_BACKEND = "claude-direct"


class AgentDispatchError(Exception):
    pass


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

    can_merge = stage == "maintain" and role == "release-manager"
    can_implement = stage == "implementation" and role in IMPLEMENTATION_ROLES
    can_write_contract_tests = stage == "contract" and role == "test-engineer"
    can_review_contract = stage == "contract-review" and role == "team-lead"

    return AgentTaskPlan(
        stage=stage,
        role=role,
        backend=effective_backend,
        can_merge=can_merge,
        can_bypass_approval=False,
        can_implement=can_implement,
        can_write_contract_tests=can_write_contract_tests,
        can_review_contract=can_review_contract,
    )
