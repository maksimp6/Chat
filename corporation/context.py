"""Who is acting right now: the role of the turn being run.

Both the cycle (a shift) and a direct order to an employee (the owner talking in that
employee's office dialog) set this, so the ``corporation`` and ``channels`` tools record
the right actor in the ledger. A ContextVar, so it is per-thread/per-task: two turns never
see each other's role.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_current_role: ContextVar[str | None] = ContextVar("corporation_current_role", default=None)

__all__ = ["current", "set_role", "reset"]


def current() -> str | None:
    return _current_role.get()


def set_role(role: str | None) -> Token:
    return _current_role.set(role)


def reset(token: Token) -> None:
    _current_role.reset(token)
