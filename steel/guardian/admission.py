"""Deterministic, fail-closed admission policy for Steel workers.

This module evaluates requests only. It does not sandbox or execute workers.
"""
from dataclasses import dataclass
from enum import Enum


class Decision(str, Enum):
    ALLOW = "ALLOW"
    MISSING_APPROVAL = "MISSING_APPROVAL"
    INVALID_LIMIT = "INVALID_LIMIT"
    CAPABILITY_DENIED = "CAPABILITY_DENIED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


@dataclass(frozen=True)
class Limits:
    wall_seconds: int
    memory_bytes: int
    cpu_seconds: int
    output_bytes: int
    token_count: int


@dataclass(frozen=True)
class Approval:
    capabilities: frozenset[str]
    ceiling: Limits
    approved: bool


@dataclass(frozen=True)
class Request:
    capabilities: frozenset[str]
    limits: Limits


def admit(request: Request, approval: Approval) -> Decision:
    """Reject absent authority, invalid bounds, escalation, or over-budget work."""
    if not approval.approved:
        return Decision.MISSING_APPROVAL
    requested = tuple(vars(request.limits).values())
    ceiling = tuple(vars(approval.ceiling).values())
    if any(type(value) is not int or value <= 0 for value in (*requested, *ceiling)):
        return Decision.INVALID_LIMIT
    if not request.capabilities.issubset(approval.capabilities):
        return Decision.CAPABILITY_DENIED
    if any(value > cap for value, cap in zip(requested, ceiling, strict=True)):
        return Decision.BUDGET_EXCEEDED
    return Decision.ALLOW
