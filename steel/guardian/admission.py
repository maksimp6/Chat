"""Deterministic, fail-closed admission decisions for Steel workers.

Admission is not a sandbox, resource limiter, or worker executor.
"""

from core.wikifunctions import trim_string

from dataclasses import dataclass
from enum import Enum
from typing import cast


KNOWN_CAPABILITIES = frozenset({"inspect"})


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
    if type(value) is not frozenset:
        return False
    capabilities = cast(frozenset[object], value)
    return all(
        type(item) is str and bool(trim_string(item)) and item in KNOWN_CAPABILITIES
        for item in capabilities
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


def _authority_valid(approval: object) -> bool:
    return (
        isinstance(approval, Approval)
        and type(approval.approved) is bool
        and approval.approved
        and _valid_capabilities(approval.capabilities)
    )


def admit(request: Request, approval: Approval) -> Decision:
    """Deny unknown capabilities, invalid bounds, and unauthorized work."""
    decision = Decision.ALLOW
    if not _authority_valid(approval):
        decision = Decision.MISSING_APPROVAL
    elif not isinstance(request, Request):
        decision = Decision.INVALID_LIMIT
    else:
        requested = _limit_values(request.limits)
        ceiling = _limit_values(approval.ceiling)
        if requested is None or ceiling is None:
            decision = Decision.INVALID_LIMIT
        elif not _valid_capabilities(request.capabilities) or not request.capabilities.issubset(approval.capabilities):
            decision = Decision.CAPABILITY_DENIED
        elif any(value > cap for value, cap in zip(requested, ceiling, strict=True)):
            decision = Decision.BUDGET_EXCEEDED
    return decision
