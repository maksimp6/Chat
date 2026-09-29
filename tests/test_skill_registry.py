from pathlib import Path

import pytest

from agent_skills.registry import (
    SkillFormatError,
    SkillPolicyError,
    SkillRegistry,
    compose_skill_instructions,
)
from trace_manager import ExecutionTrace


BODY = """## Purpose
Do one thing.

## Non-goals
Do not do unrelated work.

## Inputs
- input

## Tools
- read-only by default

## Procedure
1. Inspect.
2. Verify.

## Approval boundaries
Mutations require their normal approval.

## Validation
Verify the result.

## Failure behavior
Fail closed.

## Output
Return a concise result.
"""


def _write_skill(root: Path, name: str) -> None:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Test skill\n---\n{BODY}",
        encoding="utf-8",
    )


def test_catalog_is_metadata_only_and_load_is_explicit(tmp_path):
    root = tmp_path / ".agents" / "skills"
    _write_skill(root, "docs-sync")
    registry = SkillRegistry(root)
    catalog = registry.catalog()
    assert catalog[0]["name"] == "docs-sync"
    assert "body" not in catalog[0]
    skill = registry.load("docs-sync")
    assert "## Procedure" in skill.body
    assert len(skill.version) == 16


def test_registry_rejects_traversal_and_role_policy(tmp_path):
    root = tmp_path / ".agents" / "skills"
    _write_skill(root, "security-review")
    registry = SkillRegistry(root)
    with pytest.raises(SkillFormatError):
        registry.load("../security-review")
    with pytest.raises(SkillPolicyError):
        registry.load("security-review", role="docs-engineer")


def test_compose_and_trace_record_only_selected_skill_metadata(tmp_path):
    root = tmp_path / ".agents" / "skills"
    _write_skill(root, "docs-sync")
    skill = SkillRegistry(root).load("docs-sync")
    instructions = compose_skill_instructions([skill], base_instructions="Base")
    assert instructions.startswith("Base")
    assert "### Skill: docs-sync" in instructions
    trace = ExecutionTrace(trace_id="trace-skill")
    trace.add_skill(skill.name, source=skill.source, version=skill.version, role="docs-engineer")
    result = trace.finalize()
    assert result["skills"][0]["name"] == "docs-sync"
    assert result["skills"][0]["role"] == "docs-engineer"
    assert skill.body not in str(result)
