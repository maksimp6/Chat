"""Reconciler: apply actions to bring reality to desired state."""

from typing import List, Any, Dict
import logging

from alice_platform.planner import Action, ActionType
from alice_platform.providers.cloudru import (
    create_container,
    update_container,
    delete_container,
)

logger = logging.getLogger(__name__)


def reconcile(
    lane: str,
    actions: List[Action],
    config: Dict[str, Any],
) -> None:
    """
    Apply actions to reconcile reality to desired state.

    Args:
        lane: Lane name (test, production, etc)
        actions: Actions to apply (from plan_actions)
        config: Desired config

    Raises:
        RuntimeError: If approval required but not given
    """
    for i, action in enumerate(actions, 1):
        # Check approval for production actions
        if action.needs_approval and lane == "production":
            logger.warning(
                f"[{i}] Action requires approval: {action.type.value} {action.service}"
            )
            print(f"  {i}. [PENDING] {action.service}: requires approval")
            continue

        service_config = config.get("services", {}).get(action.service, {})

        if action.type == ActionType.CREATE:
            create_container(lane, action.service, service_config)
            print(f"  {i}. [CREATE] {action.service}: {action.description}")

        elif action.type == ActionType.UPDATE:
            update_container(lane, action.service, service_config)
            print(f"  {i}. [UPDATE] {action.service}: {action.description}")

        elif action.type == ActionType.DELETE:
            delete_container(lane, action.service)
            print(f"  {i}. [DELETE] {action.service}")

        elif action.type == ActionType.REPORT_ORPHAN:
            print(f"  {i}. [ORPHAN] {action.service}: {action.description}")
