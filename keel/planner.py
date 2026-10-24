"""Planner: generate actions from desired vs observed state."""

from enum import Enum
from dataclasses import dataclass
from typing import Any, Dict, List, Set


class ActionType(Enum):
    """Type of action to reconcile state."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    REPORT_ORPHAN = "report_orphan"


@dataclass
class Action:
    """An action to reconcile state."""

    type: ActionType
    service: str
    lane: str
    needs_approval: bool = False
    applied: bool = False
    description: str = ""


def plan_actions(
    lane: str,
    desired: Dict[str, Any],
    observed: Dict[str, Any],
) -> List[Action]:
    """
    Generate actions to reconcile desired vs observed state.

    Args:
        lane: Lane name (test, production, etc)
        desired: Desired config from config.yaml
        observed: Observed state from cloud provider

    Returns:
        List of actions to bring observed state to desired
    """
    actions: List[Action] = []

    # Extract info
    desired_services = desired.get("services", {})
    desired_lanes = desired.get("lanes", {})
    lane_config = desired_lanes.get(lane, {})
    desired_lane_services = set(lane_config.get("services", []))

    observed_containers = observed.get("containers", [])

    # Build maps for quick lookup
    observed_by_service: Dict[str, Dict[str, Any]] = {}
    for container in observed_containers:
        if container.get("lane") == lane:
            service = container.get("service")
            if service:
                observed_by_service[service] = container

    # Check each desired service
    for service_name in desired_lane_services:
        service_config = desired_services.get(service_name)
        if not service_config:
            continue

        observed_container = observed_by_service.get(service_name)
        needs_approval = lane == "production"

        if observed_container:
            # Service exists, check if update needed
            desired_scale = service_config.get("scale", 1)
            observed_scale = observed_container.get("scale", 1)

            if desired_scale != observed_scale:
                actions.append(
                    Action(
                        type=ActionType.UPDATE,
                        service=service_name,
                        lane=lane,
                        needs_approval=needs_approval,
                        description=f"Update {service_name} scale from {observed_scale} to {desired_scale}",
                    )
                )
        else:
            # Service needs to be created
            actions.append(
                Action(
                    type=ActionType.CREATE,
                    service=service_name,
                    lane=lane,
                    needs_approval=needs_approval,
                    description=f"Create {service_name}",
                )
            )

    # Check for orphaned containers (containers in observed not in desired)
    desired_lane_services_full = set(desired_lanes.get(lane, {}).get("services", []))

    for container in observed_containers:
        if container.get("lane") == lane:
            service = container.get("service")
            if service not in desired_lane_services_full:
                actions.append(
                    Action(
                        type=ActionType.REPORT_ORPHAN,
                        service=service,
                        lane=lane,
                        description=f"Orphaned container: {service}",
                    )
                )

    return actions
