"""Reconciler: apply actions to bring reality to desired state."""

from typing import List, Any, Dict

from alice_platform.planner import Action


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
        NotImplementedError: Not implemented in this slice
    """
    raise NotImplementedError("Reconciliation deferred to next slice")
