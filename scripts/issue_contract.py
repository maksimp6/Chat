"""Deterministic admission check for agent Issue bodies (#1180 slice).

A body must contain the sections of ``.github/ISSUE_TEMPLATE/agent_task.md``,
one selected repository role, and at least one non-empty acceptance item.
Historical Issues are not checked here; callers decide where it applies.
"""

from __future__ import annotations

import re

REQUIRED_SECTIONS = (
    "Goal",
    "Role",
    "Acceptance criteria",
    "Checks to run",
    "Out of scope",
)
ROLES = (
    "Team Lead",
    "Backend Engineer",
    "Frontend Engineer",
    "Android Engineer",
    "Test Engineer",
    "Infra Engineer",
    "Security Reviewer",
    "Docs Engineer",
    "Release Manager",
)
_HEADING = re.compile(r"^## (.+?)\s*$", re.MULTILINE)
_CHECKBOX = re.compile(r"^- \[([ xX])\]\s*(.*?)\s*$", re.MULTILINE)


def _sections(body: str) -> dict[str, str]:
    matches = list(_HEADING.finditer(body))
    sections = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections.setdefault(match.group(1), body[match.end() : end])
    return sections


def validate_issue_body(body: str) -> list[str]:
    """Return the problems found, in a stable order; an empty list means admitted."""
    sections = _sections(body or "")
    problems = [
        f"missing section: {name}" for name in REQUIRED_SECTIONS if name not in sections
    ]
    role_section = sections.get("Role")
    if role_section is not None:
        selected = [
            text
            for mark, text in _CHECKBOX.findall(role_section)
            if mark.lower() == "x"
        ]
        if not selected:
            problems.append("role is not selected")
        elif not any(text in ROLES for text in selected):
            problems.append("role is not a known repository role")
    criteria = sections.get("Acceptance criteria", "")
    if not any(text.strip() for _mark, text in _CHECKBOX.findall(criteria)) and (
        "Acceptance criteria" in sections
    ):
        problems.append("acceptance criteria has no item with text")
    return problems
