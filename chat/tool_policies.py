"""Named tool policies a chat request may ask for by ``tool_policy``.

A policy decides which tool actions the turn's model is offered and may run
(``execution_policy`` of the tool loop). The request names it; the object itself is
registered here by trusted code (the corporation's roles), so a name is never a
policy the model made up. An unknown name is a 400, not an open turn.
"""

from __future__ import annotations

from typing import Any

__all__ = ["get_policy", "policy_names", "register_policy"]

_POLICIES: dict[str, Any] = {}


def register_policy(name: str, policy: Any) -> None:
    _POLICIES[name] = policy


def get_policy(name: Any) -> Any | None:
    if not isinstance(name, str):
        return None
    if not _POLICIES:
        _register_known()
    policy = _POLICIES.get(name)
    if policy is None:
        policy = _hired_policy(name)
    return policy


def _hired_policy(name: str) -> Any | None:
    """The policy of an employee HR hired: the role is a ``staff.hire`` record in the
    company's ledger (written by the ``corporation hire`` tool, trusted code), not a name
    the model made up — a key the ledger does not hold is still an unknown policy."""
    try:
        from corporation.roles import hired_roles, policy_for, policy_name

        for role in hired_roles():
            if policy_name(role) == name:
                return policy_for(role)
    except Exception:  # the corporation is optional for the chat
        pass
    return None


def policy_names() -> list[str]:
    if not _POLICIES:
        _register_known()
    return sorted(_POLICIES)


def _register_known() -> None:
    try:
        from corporation.roles import ROLES, policy_for, policy_name

        for role in ROLES:
            register_policy(policy_name(role), policy_for(role))
    except Exception:  # the corporation is optional for the chat
        pass
