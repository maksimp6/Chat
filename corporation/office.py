"""Employees you can talk to directly.

Owner, 08.11.2026: "can i talk to one of the employee and he will do as i say"; and,
09.11.2026, "make it production ready ... create workers and create them static dialogs
for each". Each role has an office: a real Logos dialog titled after the employee
("🗂️ Глеб · Директор", "🧑‍💼 Кадры · Кадры"). Whatever the owner writes in that dialog is
answered by that employee — the turn runs with the employee's own instructions and all
the company's tools, outside the cycle. So the owner opens the office and gives orders;
the employee does them and writes the result into the company's ledger like any shift.

The offices are *static*, like the Court and the embeddings dialogs: each role has a
fixed conversation id (``logos-corp-<role>``), and the chat list ensures every one of
them exists and pins it (``chat.mcp_routes`` on ``GET /api/conversations``). So the
employees are always in the chat list, in the same place, after any restart or deploy —
no command has to create them. ``role_of_conversation`` is what the chat route asks to
know a dialog is an office; it answers from the fixed ids first and falls back to the
ledger's map (so a dialog made by hand before the fixed ids keeps working).

The staff is not only the built-in roles: an employee HR hired (owner, 18.11.2026:
"let hr hire more stuff"; a ``staff.hire`` record) gets an office the same way — the
rows are computed from ``roles.all_roles()`` on every listing, so a hire appears in the
chat list without a deploy.

The fixed id ↔ role mapping needs nothing stored. The ledger still gets one ``note`` per
office the first time it is created (with an ``office`` field), so the creation is in the
journal and the embeddings like every other fact, and the window can link to the dialogs.
"""

from __future__ import annotations

import logging
from typing import Any

from corporation import ledger as journal
from corporation.roles import ROLE_ORDER, all_roles, get_role

__all__ = [
    "title",
    "office_id",
    "rows",
    "role_of_conversation",
    "ensure_office",
    "ensure_all",
    "record_office",
    "offices",
]

logger = logging.getLogger("logos.corporation.office")

# A fixed, readable conversation id per role — the office never moves.
_PREFIX = "logos-corp-"


def office_id(role: str) -> str:
    return _PREFIX + role


def _office_title(employee: Any) -> str:
    """Avatar, name and job (🗂️ Глеб · Директор); an employee without a personal name —
    HR and the hired ones — is titled by the job alone (🧑‍💼 Кадры)."""
    person = employee.person
    if person["name"] == employee.title:
        return f"{person['avatar']} {employee.title}"
    return f"{person['avatar']} {person['name']} · {employee.title}"


def title(role: str) -> str:
    employee = get_role(role)
    if employee is None:
        raise ValueError(f"no such employee: {role}")
    return _office_title(employee)


def rows() -> list[tuple[str, str, str]]:
    """[(role, conversation_id, title)] for every employee, built-in and hired, in the
    company's own order (the pipeline's order, then the rest, then the hired ones). For
    the chat list and the window; computed on every call, so a hire shows up at once."""
    roles = all_roles()
    names = list(roles)

    def order(role: str) -> int:
        try:
            return ROLE_ORDER.index(role)
        except ValueError:
            return len(ROLE_ORDER) + names.index(role)

    return [
        (role, office_id(role), _office_title(roles[role]))
        for role in sorted(names, key=order)
    ]


def role_of_conversation(conv_id: Any) -> str | None:
    """The employee a dialog belongs to: a fixed office id, or a legacy ledger office."""
    if not conv_id:
        return None
    key = str(conv_id)
    if key.startswith(_PREFIX):
        role = key[len(_PREFIX) :]
        if get_role(role) is not None:
            return role
    return journal.state().offices.get(key)


def offices() -> dict[str, str]:
    """role -> conversation id, for every employee (the fixed offices)."""
    return {role: cid for role, cid, _title in rows()}


def record_office(role: str, conv: str) -> None:
    """Write the one journal note that puts an office in the ledger and the embeddings.

    Called once, the first time the dialog is created. Safe to skip (the office works
    without it); it only records the fact.
    """
    employee = get_role(role)
    if employee is None:
        return
    if journal.state().office_of.get(role) == str(conv):
        return
    journal.ledger().append(
        "note",
        "owner",
        {
            "office": role,
            "conversation": str(conv),
            "title": title(role),
            "text": f"кабинет {employee.person['name']} ({role})",
        },
    )


def ensure_office(role: str) -> str:
    """The employee's fixed dialog id; the dialog itself is created where it is ensured
    (the chat list). Records the office in the ledger the first time."""
    if get_role(role) is None:
        raise ValueError(f"no such employee: {role}")
    cid = office_id(role)
    _ensure_dialog(cid, title(role))
    record_office(role, cid)
    return cid


def ensure_all() -> list[dict[str, str]]:
    """Every employee's office; [{role, name, title, conversation}]."""
    roles = all_roles()
    result = []
    for role, cid, ttl in rows():
        _ensure_dialog(cid, ttl)
        record_office(role, cid)
        result.append(
            {
                "role": role,
                "name": roles[role].person["name"],
                "title": ttl,
                "conversation": cid,
            }
        )
    return result


def _ensure_dialog(conv_id: str, ttl: str) -> None:
    """Create the dialog in the store if it is missing, owned by the current owner.

    The chat list ensures the offices on every listing; this is the same create, used
    when an office is opened by command before the list has been drawn.
    """
    try:
        from chat.host import (
            create_conversation,
            get_conversation_title,
            get_current_owner_id,
            set_owner,
        )
    except Exception:  # pragma: no cover - the store is always present in the web process
        logger.exception("[CORP] office store unavailable")
        return
    try:
        if get_conversation_title(conv_id) is None:
            from composition import bound

            create_conversation(conv_id, ttl, bound.CHAT_MODEL, provider_pending=True)
        owner = get_current_owner_id(required=False)
        if owner:
            set_owner(conv_id, owner)
    except Exception:
        logger.exception("[CORP] could not ensure office dialog %s", conv_id)
