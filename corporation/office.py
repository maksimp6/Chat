"""Employees you can talk to directly.

Owner, 08.11.2026: "can i talk to one of the employee and he will do as i say". Each
role has an office: a real Logos dialog titled after the person ("💡 Вера · Основатель").
Whatever the owner writes in that dialog is answered by that employee — the turn runs with
the employee's own instructions and all the company's tools, outside the cycle. So the
owner opens the office and gives orders; the employee does them and writes the result into
the company's ledger like any shift.

The office dialogs are ordinary conversations; the mapping role↔conversation is kept in the
ledger (a ``note`` with an ``office`` field), so it survives restarts and the window can link
to them. ``role_of_conversation`` is what the chat route asks to know a dialog is an office.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from corporation import ledger as journal
from corporation.roles import ROLES

__all__ = ["ensure_all", "ensure_office", "offices", "role_of_conversation", "title"]


def title(role: str) -> str:
    person = ROLES[role].person
    return f"{person['avatar']} {person['name']} · {ROLES[role].title}"


def role_of_conversation(conv_id: Any) -> str | None:
    if not conv_id:
        return None
    return journal.state().offices.get(str(conv_id))


def offices() -> dict[str, str]:
    """role -> conversation id, for the roles that already have an office."""
    return dict(journal.state().office_of)


def _create_conversation(role: str) -> str:
    from ollama_client import settings

    port = os.environ.get("LOGOS_PORT") or "8080"
    body = json.dumps(
        {"title": title(role), "model": next(iter(settings.MODELS))}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/conversations",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        made = json.loads(response.read().decode("utf-8") or "{}")
    return str(made["id"])


def ensure_office(role: str) -> str:
    """The employee's dialog id, created the first time it is asked for."""
    if role not in ROLES:
        raise ValueError(f"no such employee: {role}")
    existing = journal.state().office_of.get(role)
    if existing:
        return existing
    conv = _create_conversation(role)
    journal.ledger().append(
        "note",
        "owner",
        {
            "office": role,
            "conversation": conv,
            "title": title(role),
            "text": f"кабинет {ROLES[role].person['name']} ({role})",
        },
    )
    return conv


def ensure_all() -> list[dict[str, str]]:
    """Every employee's office, created where missing; [{role, name, title, conversation}]."""
    result = []
    for role in ROLES:
        conv = ensure_office(role)
        result.append(
            {
                "role": role,
                "name": ROLES[role].person["name"],
                "title": title(role),
                "conversation": conv,
            }
        )
    return result
