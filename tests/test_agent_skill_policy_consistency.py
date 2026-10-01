"""
Behavioral contract tests for agent-skill policy consistency.
Contract: docs/agents/agent-policy-consistency-contract.md  Issue: #685

Tests run the ACTUAL public entrypoint tests/validate_skills.py via subprocess
against controlled temporary repository fixtures.  Only fixture policy data
varies (registry allowlist entries, role profiles, skill-effects.json); no mock
validators and no checker logic lives here.

[RED today]  — the baseline validator checks skill FORMAT only; it exits 0 for
              policy violations.  These tests assert the post-implementation
              nonzero exit and therefore FAIL on this branch.  They become GREEN
              once the checker defined in the contract is integrated into
              tests/validate_skills.py.

[GREEN today] — valid-input cases that already pass and must continue to pass.

Import/collection errors or a missing future helper do not count as semantic RED
evidence; the tests are written so that a missing helper simply isn't copied.
"""

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Repository paths
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]
_SKILLS_ROOT = _REPO / ".agents" / "skills"
_AGENT_SKILLS_PKG = _REPO / "agent_skills"
_VALIDATE_SCRIPT = _REPO / "tests" / "validate_skills.py"
_POLICY_HELPER = _REPO / "scripts" / "check_agent_skill_policy.py"
_POLICY_DATA = _REPO / "docs" / "agents" / "skill-effects.json"

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _base_fixture(tmp: Path) -> Path:
    """Copy real validate_skills.py, agent_skills package, and skill structures."""
    (tmp / "tests").mkdir()
    shutil.copy2(_VALIDATE_SCRIPT, tmp / "tests" / "validate_skills.py")
    shutil.copytree(_AGENT_SKILLS_PKG, tmp / "agent_skills")
    shutil.copytree(_SKILLS_ROOT, tmp / ".agents" / "skills")
    # Copy future helper only when it actually exists so the same tests exercise it later.
    if _POLICY_HELPER.exists():
        (tmp / "scripts").mkdir(exist_ok=True)
        shutil.copy2(_POLICY_HELPER, tmp / "scripts" / "check_agent_skill_policy.py")
    if _POLICY_DATA.exists():
        (tmp / "docs" / "agents").mkdir(parents=True)
        shutil.copy2(_POLICY_DATA, tmp / "docs" / "agents" / "skill-effects.json")
    return tmp


def _write_effects(tmp: Path, data: dict) -> None:
    dst = tmp / "docs" / "agents"
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "skill-effects.json").write_text(json.dumps(data))


def _write_profile(tmp: Path, filename: str, content: str) -> None:
    dst = tmp / ".github" / "agents"
    dst.mkdir(parents=True, exist_ok=True)
    (dst / filename).write_text(content)


def _patch_registry_allowlist(tmp: Path, role: str, skills: set[str]) -> None:
    """Insert one additional role entry into ROLE_SKILL_ALLOWLIST in the fixture registry."""
    reg = tmp / "agent_skills" / "registry.py"
    text = reg.read_text()
    marker = "ROLE_SKILL_ALLOWLIST = {"
    assert marker in text, "ROLE_SKILL_ALLOWLIST literal not found in fixture registry"
    skills_repr = "{" + ", ".join(repr(s) for s in sorted(skills)) + "}"
    insert = f'    "{role}": {skills_repr},\n'
    reg.write_text(text.replace(marker, marker + "\n" + insert, 1))


def _run(tmp: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(tmp / "tests" / "validate_skills.py")],
        capture_output=True,
        text=True,
        cwd=str(tmp),
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Canonical skill-effects.json: minimal complete classification for the three
# constrained roles and all skills referenced in their allowlists.
# ---------------------------------------------------------------------------

_EFFECTS: dict = {
    "version": 1,
    "roles": {
        "work-coordinator": {
            "profile": ".github/agents/work-coordinator.agent.md",
            "allowed_effects": ["read"],
            "optional": True,
        },
        "operations-observer": {
            "profile": ".github/agents/operations-observer.agent.md",
            "allowed_effects": ["read"],
            "optional": False,
        },
        "security-reviewer": {
            "profile": ".github/agents/security-reviewer.agent.md",
            "allowed_effects": ["read"],
            "optional": False,
        },
    },
    "skills": {
        "docs-sync":               {"required_effects": ["read", "edit"]},
        "security-review":         {"required_effects": ["read"]},
        "github-pr-readiness":     {"required_effects": ["read"]},
        "github-ci-diagnosis":     {"required_effects": ["read"]},
        "issue-to-pr":             {"required_effects": ["read", "edit"]},
        "alice-runtime-debugging": {"required_effects": ["read"]},
        "cloudru-change":          {"required_effects": ["read", "deploy"]},
        "release-readiness":       {"required_effects": ["read"]},
    },
}

_READONLY_PROFILE = "---\nname: Read-only\ntools: [read, search]\n---\nRead-only role.\n"
_EXECUTE_PROFILE  = "---\nname: Read-execute\ntools: [read, search, execute]\n---\nRead-mostly.\n"
_EDIT_PROFILE     = "---\nname: Edit-write\ntools: [read, write, edit]\n---\nEdit role.\n"


def _write_observer_and_reviewer(tmp: Path, profile: str = _READONLY_PROFILE) -> None:
    _write_profile(tmp, "operations-observer.agent.md", profile)
    _write_profile(tmp, "security-reviewer.agent.md", profile)


# ---------------------------------------------------------------------------
# Case 1: work-coordinator + docs-sync violates read-only policy  [RED today]
# docs-sync requires the edit effect; work-coordinator allows only read.
# Baseline exits 0; after checker integration must exit nonzero.
# ---------------------------------------------------------------------------


def test_work_coordinator_docs_sync_violates_readonly(tmp_path):
    root = _base_fixture(tmp_path)
    _patch_registry_allowlist(root, "work-coordinator", {"docs-sync"})
    _write_profile(root, "work-coordinator.agent.md", _READONLY_PROFILE)
    _write_observer_and_reviewer(root)
    _write_effects(root, _EFFECTS)
    result = _run(root)
    assert result.returncode != 0, (
        "work-coordinator selecting docs-sync (requires edit effect) must exit nonzero "
        f"under read-only policy; got 0.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


def test_work_coordinator_docs_sync_legacy_catalog_cannot_disable_gate(tmp_path):
    """A legacy note inside skill-effects.json agreeing with the wrong allowlist
    cannot suppress the policy gate.  The fixture keeps docs-sync selected."""
    root = _base_fixture(tmp_path)
    _patch_registry_allowlist(root, "work-coordinator", {"docs-sync"})
    _write_profile(root, "work-coordinator.agent.md", _READONLY_PROFILE)
    _write_observer_and_reviewer(root)
    effects = copy.deepcopy(_EFFECTS)
    effects["_legacy_note"] = "work-coordinator may use docs-sync"  # wrong legacy claim
    _write_effects(root, effects)
    result = _run(root)
    assert result.returncode != 0, (
        "legacy catalog note must not suppress the policy gate; "
        f"got 0.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


# ---------------------------------------------------------------------------
# Case 2: correct read-only skill selection passes                [GREEN today]
# operations-observer and security-reviewer select only read-effect skills.
# ---------------------------------------------------------------------------


def test_valid_readonly_skill_selection_passes(tmp_path):
    root = _base_fixture(tmp_path)
    effects = copy.deepcopy(_EFFECTS)
    del effects["roles"]["work-coordinator"]  # absent from both surfaces; skip
    _write_observer_and_reviewer(root)
    _write_effects(root, effects)
    result = _run(root)
    assert result.returncode == 0, (
        f"valid read-only skill selection must pass;\nstdout={result.stdout}\nstderr={result.stderr}"
    )


# ---------------------------------------------------------------------------
# Case 3: reviewer/observer profiles with execute tools remain permitted  [GREEN today]
# Execute alone is not evidence of mutation.
# ---------------------------------------------------------------------------


def test_readonly_role_execute_tools_permitted(tmp_path):
    root = _base_fixture(tmp_path)
    effects = copy.deepcopy(_EFFECTS)
    del effects["roles"]["work-coordinator"]
    _write_observer_and_reviewer(root, profile=_EXECUTE_PROFILE)
    _write_effects(root, effects)
    result = _run(root)
    assert result.returncode == 0, (
        "execute tools on read-only roles must be permitted; "
        f"got nonzero.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


# ---------------------------------------------------------------------------
# Case 4: referenced skill missing classification fails            [RED today]
# ---------------------------------------------------------------------------


def test_skill_missing_classification_fails(tmp_path):
    root = _base_fixture(tmp_path)
    effects = copy.deepcopy(_EFFECTS)
    del effects["roles"]["work-coordinator"]
    del effects["skills"]["github-ci-diagnosis"]  # referenced by operations-observer
    _write_observer_and_reviewer(root)
    _write_effects(root, effects)
    result = _run(root)
    assert result.returncode != 0, (
        "skill referenced in allowlist with no classification in skill-effects.json "
        f"must exit nonzero; got 0.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


# ---------------------------------------------------------------------------
# Case 5a: unknown effect name in skill classification fails       [RED today]
# ---------------------------------------------------------------------------


def test_unknown_effect_name_fails(tmp_path):
    root = _base_fixture(tmp_path)
    effects = copy.deepcopy(_EFFECTS)
    del effects["roles"]["work-coordinator"]
    effects["skills"]["security-review"]["required_effects"] = ["read", "unknown-effect"]
    _write_observer_and_reviewer(root)
    _write_effects(root, effects)
    result = _run(root)
    assert result.returncode != 0, (
        "unknown effect name 'unknown-effect' must cause nonzero exit; "
        f"got 0.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


# Case 5b: invalid schema version fails                           [RED today]


def test_invalid_schema_version_fails(tmp_path):
    root = _base_fixture(tmp_path)
    effects = copy.deepcopy(_EFFECTS)
    del effects["roles"]["work-coordinator"]
    effects["version"] = 99
    _write_observer_and_reviewer(root)
    _write_effects(root, effects)
    result = _run(root)
    assert result.returncode != 0, (
        "unsupported schema version 99 must cause nonzero exit; "
        f"got 0.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


# ---------------------------------------------------------------------------
# Case 6: read-only profile with explicit edit/write tools fails  [RED today]
# ---------------------------------------------------------------------------


def test_readonly_role_edit_tools_rejected(tmp_path):
    root = _base_fixture(tmp_path)
    effects = copy.deepcopy(_EFFECTS)
    del effects["roles"]["work-coordinator"]
    _write_observer_and_reviewer(root)
    _write_profile(root, "operations-observer.agent.md", _EDIT_PROFILE)
    _write_effects(root, effects)
    result = _run(root)
    assert result.returncode != 0, (
        "read-only operations-observer profile listing edit/write tools must exit nonzero; "
        f"got 0.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


# ---------------------------------------------------------------------------
# Case 7: optional role absent on BOTH surfaces → NOT_PRESENT, passes  [GREEN today]
# work-coordinator has optional=True; current registry has no entry for it
# and no profile is written — checker must report NOT_PRESENT and exit 0.
# ---------------------------------------------------------------------------


def test_optional_role_absent_both_passes(tmp_path):
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    _write_effects(root, _EFFECTS)  # work-coordinator kept as optional=True
    # No _patch_registry_allowlist and no profile for work-coordinator.
    result = _run(root)
    assert result.returncode == 0, (
        "optional role absent on both surfaces must exit 0 (NOT_PRESENT); "
        f"got nonzero.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


# ---------------------------------------------------------------------------
# Case 8: optional role present on only one surface fails         [RED today]
# ---------------------------------------------------------------------------


def test_optional_role_profile_present_registry_absent_fails(tmp_path):
    """Profile file exists for work-coordinator but registry has no entry."""
    root = _base_fixture(tmp_path)
    _write_profile(root, "work-coordinator.agent.md", _READONLY_PROFILE)
    _write_observer_and_reviewer(root)
    _write_effects(root, _EFFECTS)
    result = _run(root)
    assert result.returncode != 0, (
        "work-coordinator profile present but absent from registry must exit nonzero; "
        f"got 0.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


def test_optional_role_registry_present_profile_absent_fails(tmp_path):
    """Registry entry exists for work-coordinator but profile file is absent."""
    root = _base_fixture(tmp_path)
    _patch_registry_allowlist(root, "work-coordinator", {"security-review"})
    _write_observer_and_reviewer(root)
    _write_effects(root, _EFFECTS)
    # No profile written for work-coordinator.
    result = _run(root)
    assert result.returncode != 0, (
        "work-coordinator in registry but no profile must exit nonzero; "
        f"got 0.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


# ---------------------------------------------------------------------------
# Case 9a: skill-effects.json missing entirely fails               [RED today]
# ---------------------------------------------------------------------------


def test_missing_policy_data_fails(tmp_path):
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    # Deliberately do NOT write skill-effects.json.
    result = _run(root)
    assert result.returncode != 0, (
        "absent skill-effects.json must cause nonzero exit; "
        f"got 0.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


# Case 9b: symlinked skill directory excluded from discovery      [GREEN today]
# SkillRegistry._names already excludes symlinked dirs.


def test_symlinked_skill_dir_excluded_not_crash(tmp_path):
    root = _base_fixture(tmp_path)
    effects = copy.deepcopy(_EFFECTS)
    del effects["roles"]["work-coordinator"]
    _write_observer_and_reviewer(root)
    _write_effects(root, effects)
    real = root / ".agents" / "skills" / "security-review"
    (root / ".agents" / "skills" / "sym-skill").symlink_to(real)
    result = _run(root)
    assert result.returncode == 0, (
        "symlinked skill dir must be silently excluded, not cause a crash; "
        f"got nonzero.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


# Case 9c: symlinked role profile rejected                        [RED today]


def test_symlinked_role_profile_rejected(tmp_path):
    root = _base_fixture(tmp_path)
    effects = copy.deepcopy(_EFFECTS)
    del effects["roles"]["work-coordinator"]
    agents_dir = root / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    real = root / ".agents" / "skills" / "security-review" / "SKILL.md"
    (agents_dir / "security-reviewer.agent.md").symlink_to(real)
    (agents_dir / "operations-observer.agent.md").write_text(_READONLY_PROFILE)
    _write_effects(root, effects)
    result = _run(root)
    assert result.returncode != 0, (
        "symlinked role profile must cause nonzero exit; "
        f"got 0.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


# ---------------------------------------------------------------------------
# Case 10a: dynamic registry assignment fails                     [RED today]
# AST-based parser must reject mutation of ROLE_SKILL_ALLOWLIST after literal.
# ---------------------------------------------------------------------------


def test_dynamic_registry_assignment_fails(tmp_path):
    root = _base_fixture(tmp_path)
    reg = root / "agent_skills" / "registry.py"
    reg.write_text(reg.read_text() + '\nROLE_SKILL_ALLOWLIST["extra"] = {"security-review"}\n')
    effects = copy.deepcopy(_EFFECTS)
    del effects["roles"]["work-coordinator"]
    _write_observer_and_reviewer(root)
    _write_effects(root, effects)
    result = _run(root)
    assert result.returncode != 0, (
        "dynamic ROLE_SKILL_ALLOWLIST mutation must cause nonzero exit; "
        f"got 0.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


# Case 10b: duplicate registry assignment fails                   [RED today]


def test_duplicate_registry_assignment_fails(tmp_path):
    root = _base_fixture(tmp_path)
    reg = root / "agent_skills" / "registry.py"
    reg.write_text(
        reg.read_text() + '\nROLE_SKILL_ALLOWLIST = {"duplicate": {"security-review"}}\n'
    )
    effects = copy.deepcopy(_EFFECTS)
    del effects["roles"]["work-coordinator"]
    _write_observer_and_reviewer(root)
    _write_effects(root, effects)
    result = _run(root)
    assert result.returncode != 0, (
        "duplicate ROLE_SKILL_ALLOWLIST assignment must cause nonzero exit; "
        f"got 0.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


# Case 10c: duplicate JSON keys in skill-effects.json fails       [RED today]


def test_duplicate_json_keys_in_effects_fails(tmp_path):
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    dst = root / "docs" / "agents"
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "skill-effects.json").write_text(
        '{"version": 1, "roles": {}, "skills": {'
        '"security-review": {"required_effects": ["read"]}, '
        '"security-review": {"required_effects": ["edit"]}}}'
    )
    result = _run(root)
    assert result.returncode != 0, (
        "duplicate JSON keys in skill-effects.json must cause nonzero exit; "
        f"got 0.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


# ---------------------------------------------------------------------------
# Case 11: diagnostics are deterministic; checker makes no writes [GREEN today]
# ---------------------------------------------------------------------------


def test_deterministic_output_no_writes(tmp_path):
    root = _base_fixture(tmp_path)
    effects = copy.deepcopy(_EFFECTS)
    del effects["roles"]["work-coordinator"]
    _write_observer_and_reviewer(root)
    _write_effects(root, effects)
    r1 = _run(root)
    r2 = _run(root)
    assert r1.returncode == r2.returncode, "exit code must be deterministic across runs"
    assert r1.stdout == r2.stdout, "stdout must be identical across runs"
    files_before = frozenset(p for p in root.rglob("*") if p.is_file())
    _run(root)
    files_after = frozenset(p for p in root.rglob("*") if p.is_file())
    new_files = files_after - files_before
    assert not new_files, f"checker must not write files; new files found: {new_files}"
