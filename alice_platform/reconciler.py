"""Reconciler: validate desired-state actions without a configured cloud backend."""

from typing import Any, Dict, List
import logging

from alice_platform.planner import Action, ActionType

logger = logging.getLogger(__name__)


def reconcile(lane: str, actions: List[Action], config: Dict[str, Any]) -> None:
    """Process reconciliation actions and fail closed for provider mutations.

    A concrete provider adapter must be installed before CREATE, UPDATE, or DELETE
    actions can be applied. Reporting orphaned resources remains provider-neutral.
    """
    for i, action in enumerate(actions, 1):
        if action.needs_approval and lane == "production":
            logger.warning("[%s] Action requires approval: %s %s", i, action.type.value, action.service)
            print(f"  {i}. [PENDING] {action.service}: requires approval")
            continue
        if action.type == ActionType.REPORT_ORPHAN:
            print(f"  {i}. [ORPHAN] {action.service}: {action.description}")
            continue
        raise RuntimeError("No infrastructure provider adapter is configured")
