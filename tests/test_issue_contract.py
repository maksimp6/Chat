"""Agent Issue contract: a task must name owner, criteria and checks (#1180)."""

from pathlib import Path

import pytest

from scripts.issue_contract import ROLES, validate_issue_body

COMPLETE = """## Goal

Make the thing work.

## Role

- [x] Backend Engineer

## Acceptance criteria

- [x] the thing returns the right value

## Checks to run

- [ ] `pytest -q tests/test_thing.py`

## Out of scope

Nothing else.
"""


def _without(section):
    lines, skipping = [], False
    for line in COMPLETE.splitlines(keepends=True):
        if line.startswith("## "):
            skipping = line.strip() == f"## {section}"
        if not skipping:
            lines.append(line)
    return "".join(lines)


def test_complete_task_is_accepted():
    assert validate_issue_body(COMPLETE) == []


@pytest.mark.parametrize(
    "section",
    ["Goal", "Role", "Acceptance criteria", "Checks to run", "Out of scope"],
)
def test_missing_section_is_named(section):
    problems = validate_issue_body(_without(section))
    assert f"missing section: {section}" in problems


def test_empty_body_is_rejected_with_every_section_named():
    problems = validate_issue_body("")
    assert len([p for p in problems if p.startswith("missing section")]) == 5


def test_template_checkboxes_are_not_a_contract():
    template = COMPLETE.replace("- [x] the thing returns the right value", "- [ ]")
    assert "acceptance criteria has no item with text" in validate_issue_body(template)


def test_role_must_be_a_known_repository_role():
    body = COMPLETE.replace("- [x] Backend Engineer", "- [x] Some Model")
    assert "role is not a known repository role" in validate_issue_body(body)


def test_role_must_be_ticked():
    body = COMPLETE.replace("- [x] Backend Engineer", "- [ ] Backend Engineer")
    assert "role is not selected" in validate_issue_body(body)


def test_all_repository_roles_from_template_are_known():
    root = Path(__file__).resolve().parents[1]
    template = (root / ".github" / "ISSUE_TEMPLATE" / "agent_task.md").read_text(encoding="utf-8")
    for role in ROLES:
        assert f"- [ ] {role}" in template


def test_validation_is_deterministic():
    body = _without("Goal")
    assert validate_issue_body(body) == validate_issue_body(body)
