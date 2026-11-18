"""Who is acting right now: the role of the turn being run, and whose brain it is.

Both the cycle (a shift) and a direct order to an employee (the owner talking in that
employee's office dialog) set the role, so the ``corporation`` and ``channels`` tools record
the right actor in the ledger. A ContextVar, so it is per-thread/per-task: two turns never
see each other's role.

A shift done by a worker outside the box (``corporation.outside``: a strong model that is
not the local runner) also sets the *brain* — the name of the model that did the work.
The ledger stamps it into every record written during that shift (``data.via``), so the
journal never passes outside work off as the local runner's.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_current_role: ContextVar[str | None] = ContextVar(
    "corporation_current_role", default=None
)
_current_brain: ContextVar[str | None] = ContextVar(
    "corporation_current_brain", default=None
)

__all__ = ["brain", "current", "reset", "reset_brain", "set_brain", "set_role"]


def current() -> str | None:
    return _current_role.get()


def set_role(role: str | None) -> Token:
    return _current_role.set(role)


def reset(token: Token) -> None:
    _current_role.reset(token)


def brain() -> str | None:
    """The model doing an outside shift; None for the local runner's own turns."""
    return _current_brain.get()


def set_brain(name: str | None) -> Token:
    return _current_brain.set(name)


def reset_brain(token: Token) -> None:
    _current_brain.reset(token)
