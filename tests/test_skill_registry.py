from pathlib import Path

import pytest

from agent_skills.registry import (
    MAX_SELECTED_SKILLS,
    SkillFormatError,
    SkillNotFoundError,
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


def _write_raw(root: Path, name: str, content: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SKILL.md"
    path.write_text(content, encoding="utf-8")
    return path


def _write_skill(root: Path, name: str, description: str = "Test skill") -> Path:
    return _write_raw(
        root,
        name,
        f"---\nname: {name}\ndescription: {description}\n---\n{BODY}",
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


def test_registry_rejects_invalid_names_missing_files_and_symlinks(tmp_path):
    root = tmp_path / ".agents" / "skills"
    root.mkdir(parents=True)
    registry = SkillRegistry(root)

    with pytest.raises(SkillFormatError):
        registry.load("../security-review")
    with pytest.raises(SkillNotFoundError):
        registry.load("missing-skill")

    target = tmp_path / "symlink-target"
    target.mkdir()
    (root / "docs-sync").symlink_to(target, target_is_directory=True)
    with pytest.raises(SkillFormatError):
        registry.load("docs-sync")


def test_frontmatter_validation_is_fail_closed(tmp_path):
    root = tmp_path / ".agents" / "skills"
    registry = SkillRegistry(root)

    _write_raw(root, "docs-sync", BODY)
    with pytest.raises(SkillFormatError, match="must start"):
        registry.load("docs-sync")

    _write_raw(
        root,
        "docs-sync",
        "---\n# comment\n\nname: docs-sync\ndescription: ok\n---\n" + BODY,
    )
    assert registry.load("docs-sync").description == "ok"

    _write_raw(root, "docs-sync", "---\nname docs-sync\n---\n" + BODY)
    with pytest.raises(SkillFormatError, match="invalid frontmatter"):
        registry.load("docs-sync")

    _write_raw(root, "docs-sync", "---\nname: docs-sync\ndescription: no close\n")
    with pytest.raises(SkillFormatError, match="not closed"):
        registry.load("docs-sync")

    _write_raw(root, "docs-sync", "---\nname: docs-sync\n---\n" + BODY)
    with pytest.raises(SkillFormatError, match="missing frontmatter field"):
        registry.load("docs-sync")

    _write_raw(
        root,
        "docs-sync",
        "---\nname: security-review\ndescription: mismatch\n---\n" + BODY,
    )
    with pytest.raises(SkillFormatError, match="must match directory"):
        registry.load("docs-sync")


def test_registry_role_catalog_and_empty_root_policy(tmp_path):
    missing_root = tmp_path / "missing"
    missing = SkillRegistry(missing_root)
    assert missing.catalog() == []
    with pytest.raises(SkillFormatError, match="no skills found"):
        missing.validate_all()

    root = tmp_path / ".agents" / "skills"
    _write_skill(root, "docs-sync")
    _write_skill(root, "security-review")
    registry = SkillRegistry(root)

    assert [item["name"] for item in registry.catalog(role="Docs Engineer")] == ["docs-sync"]
    assert [item["name"] for item in registry.catalog(role="Operations Observer")] == [
        "security-review"
    ]
    assert [item["name"] for item in registry.catalog(role="Work Coordinator")] == ["docs-sync"]
    assert [item["name"] for item in registry.catalog(role="Process Governor")] == [
        "docs-sync",
        "security-review",
    ]
    assert [item["name"] for item in registry.validate_all()] == [
        "docs-sync",
        "security-review",
    ]
    with pytest.raises(SkillPolicyError, match="unknown role"):
        registry.catalog(role="Mystery Wizard")
    with pytest.raises(SkillPolicyError):
        registry.load("security-review", role="docs-engineer")


def test_load_rejects_oversize_missing_sections_and_unclosed_body(tmp_path, monkeypatch):
    root = tmp_path / ".agents" / "skills"
    path = _write_skill(root, "docs-sync")
    registry = SkillRegistry(root)

    path.write_text(
        "---\nname: docs-sync\ndescription: huge\n---\n" + BODY + ("x" * (70 * 1024)),
        encoding="utf-8",
    )
    with pytest.raises(SkillFormatError, match="exceeds"):
        registry.load("docs-sync")

    _write_raw(
        root,
        "docs-sync",
        "---\nname: docs-sync\ndescription: missing output\n---\n"
        + BODY.replace("## Output\nReturn a concise result.\n", ""),
    )
    with pytest.raises(SkillFormatError, match="missing section"):
        registry.load("docs-sync")

    _write_raw(root, "docs-sync", "---\nname: docs-sync\ndescription: broken")
    monkeypatch.setattr(
        registry,
        "_read_metadata",
        lambda _name: {"name": "docs-sync", "description": "broken"},
    )
    with pytest.raises(SkillFormatError, match="frontmatter is not closed"):
        registry.load("docs-sync")


def test_load_many_deduplicates_and_enforces_limit(tmp_path):
    root = tmp_path / ".agents" / "skills"
    _write_skill(root, "docs-sync")
    registry = SkillRegistry(root)

    loaded = registry.load_many(["docs-sync", "docs-sync"])
    assert [skill.name for skill in loaded] == ["docs-sync"]

    with pytest.raises(SkillPolicyError, match="at most"):
        registry.load_many([f"skill-{index}" for index in range(MAX_SELECTED_SKILLS + 1)])

    def too_many_unique_names():
        for index in range(MAX_SELECTED_SKILLS + 1):
            yield f"skill-{index}"
        raise AssertionError("load_many iterated beyond the first rejected unique skill")

    with pytest.raises(SkillPolicyError, match="at most"):
        registry.load_many(too_many_unique_names())


def test_compose_and_trace_record_only_selected_skill_metadata(tmp_path):
    root = tmp_path / ".agents" / "skills"
    _write_skill(root, "docs-sync")
    skill = SkillRegistry(root).load("docs-sync")

    instructions = compose_skill_instructions([skill], base_instructions="Base")
    assert instructions.startswith("Base")
    assert "### Skill: docs-sync" in instructions

    trace = ExecutionTrace(trace_id="trace-skill")
    trace.add_skill(
        skill.name,
        source=skill.source,
        version=skill.version,
        role="docs-engineer",
    )
    trace.add_skill(
        skill.name,
        source=skill.source,
        version=skill.version,
        role="docs-engineer",
    )
    result = trace.finalize()

    assert result["skills"] == [
        {
            "name": "docs-sync",
            "source": skill.source,
            "version": skill.version,
            "role": "docs-engineer",
        }
    ]
    assert skill.body not in str(result)
