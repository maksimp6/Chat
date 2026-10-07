"""Desired-state reconciler for Alice infrastructure."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from alice_platform.planner import Action, ActionType
from cloud.registry import ensure_default_providers, resolve_provider_name

logger = logging.getLogger(__name__)


def reconcile(lane: str, actions: List[Action], config: Dict[str, Any]) -> None:
    """Apply desired-state actions through the selected infrastructure provider.

    Production mutations remain approval-gated by the planner. Orphans are
    reported but never deleted implicitly.
    """

    cloud_config = config.get("cloud", {})
    if not isinstance(cloud_config, dict):
        raise ValueError("cloud configuration must be an object")

    provider_name = resolve_provider_name(cloud_config)
    provider = ensure_default_providers().get(provider_name)

    for index, action in enumerate(actions, 1):
        if action.needs_approval and lane == "production":
            logger.warning(
                "[%s] Action requires approval: %s %s",
                index,
                action.type.value,
                action.service,
            )
            print(f"  {index}. [PENDING] {action.service}: requires approval")
            continue

        if action.type == ActionType.REPORT_ORPHAN:
            print(f"  {index}. [ORPHAN] {action.service}: {action.description}")
            continue

        service_config = config.get("services", {}).get(action.service, {})
        result = provider.reconcile_container(
            operation=action.type.value,
            lane=lane,
            service=action.service,
            config=service_config,
        )
        action.applied = True
        print(
            f"  {index}. [{action.type.value.upper()}] "
            f"{action.service}: {result.get('state', 'accepted')}"
        )
