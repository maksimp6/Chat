"""Deterministic, fail-closed admission decisions for Steel workers.

Admission is not a sandbox, resource limiter, or worker executor.
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


def _valid_capabilities(value: object) -> bool:
    return (
        type(value) is frozenset
        and all(type(item) is str and bool(item.strip()) for item in value)
    )


def _limit_values(value: object) -> tuple[int, ...] | None:
    if not isinstance(value, Limits):
        return None
    fields = (
        value.wall_seconds,
        value.memory_bytes,
        value.cpu_seconds,
        value.output_bytes,
        value.token_count,
    )
    if any(type(item) is not int or item <= 0 for item in fields):
        return None
    return fields


def admit(request: Request, approval: Approval) -> Decision:
    """Deny invalid authority and limits before considering worker admission."""
    if not isinstance(approval, Approval) or type(approval.approved) is not bool:
        return Decision.MISSING_APPROVAL
    if not approval.approved:
        return Decision.MISSING_APPROVAL
    if not isinstance(request, Request):
        return Decision.INVALID_LIMIT
    requested = _limit_values(request.limits)
    ceiling = _limit_values(approval.ceiling)
    if requested is None or ceiling is None:
        return Decision.INVALID_LIMIT
    if not _valid_capabilities(request.capabilities):
        return Decision.CAPABILITY_DENIED
    if not _valid_capabilities(approval.capabilities):
        return Decision.CAPABILITY_DENIED
    if not request.capabilities.issubset(approval.capabilities):
        return Decision.CAPABILITY_DENIED
    if any(value > cap for value, cap in zip(requested, ceiling, strict=True)):
        return Decision.BUDGET_EXCEEDED
    return Decision.ALLOW
