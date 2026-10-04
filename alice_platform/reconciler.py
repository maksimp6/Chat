"""Bounded reconciler for Alice Platform resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List

from alice_platform.planner import Action


SAFE_OPERATIONS = frozenset({"start", "restart", "redeploy_same_revision"})


@dataclass(frozen=True)
class ApplyResult:
    service: str
    operation: str
    status: str


def reconcile(
    lane: str,
    actions: List[Action],
    config: Dict[str, Any],
    *,
    executors: Dict[str, Callable[[str, Dict[str, Any]], None]] | None = None,
    approve: bool = False,
) -> List[ApplyResult]:
    """Apply an already-reviewed plan using an explicit operation allowlist.

    Production mutations require approval. Destructive replacement is intentionally
    absent from SAFE_OPERATIONS.
    """
    if lane not in {"test", "production"}:
        raise ValueError(f"unknown lane: {lane}")
    executors = executors or {}
    results: List[ApplyResult] = []
    for action in actions:
        service = getattr(action, "service", None) or getattr(action, "resource", None)
        operation = getattr(action, "operation", None) or getattr(action, "action", None)
        if not isinstance(service, str) or not isinstance(operation, str):
            raise ValueError("action must identify service and operation")
        operation = operation.lower()
        if operation not in SAFE_OPERATIONS:
            raise ValueError(f"unsafe reconciliation operation: {operation}")
        if lane == "production" and not approve:
            raise PermissionError("production reconciliation requires approval")
        executor = executors.get(operation)
        if executor is None:
            raise RuntimeError(f"no executor registered for {operation}")
        executor(service, config)
        results.append(ApplyResult(service, operation, "applied"))
    return results
