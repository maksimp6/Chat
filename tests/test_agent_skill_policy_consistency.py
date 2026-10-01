"""
Behavioral contract tests for agent-skill policy consistency.
Contract: docs/agents/agent-policy-consistency-contract.md  Issue: #685

Tests run the ACTUAL public entrypoint tests/validate_skills.py via subprocess
against controlled temporary repository fixtures.  Only fixture policy data
varies (registry allowlist entries, role profiles, skill-effects.json); no mock
validators and no checker logic lives here.

[RED today]  — the baseline validator checks skill FORMAT only; it exits 0 for
              policy violations.  These tests assert the post-implementation
              nonzero exit AND a cause-specific diagnostic token, and therefore
              FAIL on this branch.  They become GREEN once the checker defined in
              the contract is integrated into tests/validate_skills.py.

[GREEN today] — valid-input cases that already pass and must continue to pass.

Import/collection errors or a missing future helper do not count as semantic RED
evidence; the tests are written so that a missing helper simply isn't copied.
"""

import ast
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

# ---------------------------------------------------------------------------
# Diagnostic class tokens — implementer must emit at least one per failure.
# Documented in docs/agents/agent-policy-consistency-contract.md §Diagnostic tokens.
# ---------------------------------------------------------------------------

_TOKEN_POLICY_VIOLATION = "POLICY_VIOLATION"
_TOKEN_MISSING_CLASS = "MISSING_CLASSIFICATION"
_TOKEN_SCHEMA_ERROR = "SCHEMA_ERROR"
_TOKEN_UNSAFE_PATH = "UNSAFE_PATH"
_TOKEN_REGISTRY_ERROR = "REGISTRY_ERROR"
_TOKEN_ONE_SIDED = "ONE_SIDED_OPTIONAL"
_TOKEN_MISSING_FILE = "MISSING_FILE"
_TOKEN_MISSING_ROLE_META = "MISSING_ROLE_METADATA"

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _base_fixture(tmp: Path) -> Path:
    """Copy real validate_skills.py, agent_skills package, and skill structures.

    Does NOT copy skill-effects.json — every test builds its policy explicitly.
    Explicitly removes work-coordinator from the registry allowlist so that
    future #655 merges cannot silently change fixture behaviour.
    """
    (tmp / "tests").mkdir()
    shutil.copy2(_VALIDATE_SCRIPT, tmp / "tests" / "validate_skills.py")
    shutil.copytree(_AGENT_SKILLS_PKG, tmp / "agent_skills")
    shutil.copytree(_SKILLS_ROOT, tmp / ".agents" / "skills")
    # Copy future helper only when it actually exists so the same tests exercise it later.
    if _POLICY_HELPER.exists():
        (tmp / "scripts").mkdir(exist_ok=True)
        shutil.copy2(_POLICY_HELPER, tmp / "scripts" / "check_agent_skill_policy.py")
    # Remove optional work-coordinator entry so #655 merge cannot affect fixtures.
    _remove_from_allowlist(tmp, "work-coordinator")
    return tmp


def _read_allowlist(text: str) -> tuple[dict, int, int]:
    """Parse ROLE_SKILL_ALLOWLIST literal from source; return (dict, start, end)."""
    marker = "ROLE_SKILL_ALLOWLIST = "
    assert marker in text, "ROLE_SKILL_ALLOWLIST literal not found in fixture registry"
    start = text.index(marker) + len(marker)
    depth = 0
    end = start
    for i, ch in enumerate(text[start:]):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = start + i + 1
                break
    parsed = ast.literal_eval(text[start:end])
    return parsed, start, end


def _write_allowlist_dict(tmp: Path, updated: dict) -> None:
    """Replace ROLE_SKILL_ALLOWLIST in fixture registry.py with updated dict."""
    reg = tmp / "agent_skills" / "registry.py"
    text = reg.read_text()
    _, start, end = _read_allowlist(text)
    inner = "".join(
        "    {!r}: {{{}}},\n".format(k, ", ".join(repr(s) for s in sorted(v)))
        for k, v in updated.items()
    )
    reg.write_text(text[:start] + "{\n" + inner + "}" + text[end:])


def _patch_registry_allowlist(tmp: Path, role: str, skills: set) -> None:
    """Add or replace one role entry in ROLE_SKILL_ALLOWLIST (no duplicate keys)."""
    reg = tmp / "agent_skills" / "registry.py"
    d, _, _ = _read_allowlist(reg.read_text())
    d[role] = skills
    _write_allowlist_dict(tmp, d)


def _remove_from_allowlist(tmp: Path, role: str) -> None:
    """Remove a role from ROLE_SKILL_ALLOWLIST if present."""
    reg = tmp / "agent_skills" / "registry.py"
    d, _, _ = _read_allowlist(reg.read_text())
    d.pop(role, None)
    _write_allowlist_dict(tmp, d)


def _write_effects(tmp: Path, data: dict) -> None:
    dst = tmp / "docs" / "agents"
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "skill-effects.json").write_text(json.dumps(data))


def _write_profile(tmp: Path, filename: str, content: str) -> None:
    dst = tmp / ".github" / "agents"
    dst.mkdir(parents=True, exist_ok=True)
    (dst / filename).write_text(content)


def _run(tmp: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(tmp / "tests" / "validate_skills.py")],
        capture_output=True,
        text=True,
        cwd=str(tmp),
        timeout=30,
    )


def _assert_diagnostic(result: subprocess.CompletedProcess, *tokens: str) -> None:
    """Assert nonzero exit AND at least one expected cause-specific token in output.

    A generic crash or import error that happens to exit nonzero does not satisfy
    this check because it will not contain the expected class token.
    """
    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"expected nonzero exit; got 0.\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert any(t in combined for t in tokens), (
        f"expected one of {tokens!r} in combined output;\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Canonical skill-effects.json: minimal complete classification for the three
# constrained roles and skills referenced in their allowlists / test fixtures.
#
# D7 corrections: alice-runtime-debugging → read+edit (Procedure step 6 adds a
# regression test); cloudru-change → read+edit+deploy (applies cloud changes).
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
        "docs-sync": {"required_effects": ["read", "edit"]},
        "security-review": {"required_effects": ["read"]},
        "github-pr-readiness": {"required_effects": ["read"]},
        "github-ci-diagnosis": {"required_effects": ["read"]},
        "issue-to-pr": {"required_effects": ["read", "edit"]},
        "alice-runtime-debugging": {"required_effects": ["read", "edit"]},
        "cloudru-change": {"required_effects": ["read", "edit", "deploy"]},
        "release-readiness": {"required_effects": ["read"]},
    },
}

_READONLY_PROFILE = "---\nname: Read-only\ntools: [read, search]\n---\nRead-only role.\n"
_EXECUTE_PROFILE = "---\nname: Read-execute\ntools: [read, search, execute]\n---\nRead-mostly.\n"
_EDIT_PROFILE = "---\nname: Edit-write\ntools: [read, write, edit]\n---\nEdit role.\n"


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
    _assert_diagnostic(result, _TOKEN_POLICY_VIOLATION)


def test_work_coordinator_docs_sync_legacy_catalog_cannot_disable_gate(tmp_path):
    """Legacy note inside skill-effects.json cannot suppress the policy gate.

    First asserts that the real SkillRegistry.catalog reports docs-sync for
    work-coordinator (proving the legacy positive path exists), then asserts
    the independent policy gate still rejects the fixture.
    """
    root = _base_fixture(tmp_path)
    _patch_registry_allowlist(root, "work-coordinator", {"docs-sync"})
    _write_profile(root, "work-coordinator.agent.md", _READONLY_PROFILE)
    _write_observer_and_reviewer(root)
    effects = copy.deepcopy(_EFFECTS)
    _write_effects(root, effects)

    # Step 1: prove the real SkillRegistry.catalog returns docs-sync for work-coordinator.
    catalog_result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.path.insert(0, '.'); "
                "from agent_skills.registry import SkillRegistry; "
                "r = SkillRegistry('.agents/skills'); "
                "names = [s['name'] for s in r.catalog('work-coordinator')]; "
                "assert 'docs-sync' in names, 'docs-sync not in catalog: ' + repr(names)"
            ),
        ],
        capture_output=True,
        text=True,
        cwd=str(root),
        timeout=15,
    )
    assert catalog_result.returncode == 0, (
        "expected docs-sync in work-coordinator catalog (legacy path);\n"
        f"stderr={catalog_result.stderr}"
    )

    # Step 2: independent policy gate must reject despite the legacy catalog entry.
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_POLICY_VIOLATION)


# ---------------------------------------------------------------------------
# Case 2: correct read-only skill selection passes                [GREEN today]
# operations-observer and security-reviewer select only read-effect skills.
# work-coordinator is optional and absent on both surfaces → NOT_PRESENT, OK.
# ---------------------------------------------------------------------------


def test_valid_readonly_skill_selection_passes(tmp_path):
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    _write_effects(root, _EFFECTS)
    result = _run(root)
    assert result.returncode == 0, (
        f"valid read-only skill selection must pass;\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


# ---------------------------------------------------------------------------
# Case 3: reviewer/observer profiles with execute tools remain permitted  [GREEN today]
# Execute alone is not evidence of mutation.
# ---------------------------------------------------------------------------


def test_readonly_role_execute_tools_permitted(tmp_path):
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root, profile=_EXECUTE_PROFILE)
    _write_effects(root, _EFFECTS)
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
    del effects["skills"]["github-ci-diagnosis"]  # referenced by operations-observer
    _write_observer_and_reviewer(root)
    _write_effects(root, effects)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_MISSING_CLASS)


# ---------------------------------------------------------------------------
# Case 5a: unknown effect name in skill classification fails       [RED today]
# ---------------------------------------------------------------------------


def test_unknown_effect_name_fails(tmp_path):
    root = _base_fixture(tmp_path)
    effects = copy.deepcopy(_EFFECTS)
    effects["skills"]["security-review"]["required_effects"] = ["read", "unknown-effect"]
    _write_observer_and_reviewer(root)
    _write_effects(root, effects)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_SCHEMA_ERROR)


# Case 5b: invalid schema version fails                           [RED today]


def test_invalid_schema_version_fails(tmp_path):
    root = _base_fixture(tmp_path)
    effects = copy.deepcopy(_EFFECTS)
    effects["version"] = 99
    _write_observer_and_reviewer(root)
    _write_effects(root, effects)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_SCHEMA_ERROR)


# ---------------------------------------------------------------------------
# Case 6: read-only profile with explicit edit/write tools fails  [RED today]
# ---------------------------------------------------------------------------


def test_readonly_role_edit_tools_rejected(tmp_path):
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    _write_profile(root, "operations-observer.agent.md", _EDIT_PROFILE)
    _write_effects(root, _EFFECTS)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_POLICY_VIOLATION)


# ---------------------------------------------------------------------------
# Case 7: optional role absent on BOTH surfaces → NOT_PRESENT, passes  [GREEN today]
# work-coordinator has optional=True; _base_fixture removes it from registry
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
    combined = result.stdout + result.stderr
    assert "NOT_PRESENT" in combined, (
        "checker must explicitly report NOT_PRESENT for absent optional role; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
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
    _assert_diagnostic(result, _TOKEN_ONE_SIDED)


def test_optional_role_registry_present_profile_absent_fails(tmp_path):
    """Registry entry exists for work-coordinator but profile file is absent."""
    root = _base_fixture(tmp_path)
    _patch_registry_allowlist(root, "work-coordinator", {"security-review"})
    _write_observer_and_reviewer(root)
    _write_effects(root, _EFFECTS)
    # No profile written for work-coordinator.
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_ONE_SIDED)


# ---------------------------------------------------------------------------
# Case 9a: skill-effects.json missing entirely fails               [RED today]
# _base_fixture never copies policy JSON; this test deliberately omits it.
# ---------------------------------------------------------------------------


def test_missing_policy_data_fails(tmp_path):
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    # Deliberately do NOT call _write_effects — no skill-effects.json in fixture.
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_MISSING_FILE)


# Case 9b: unreferenced symlinked skill directory excluded from discovery  [GREEN today]
# SkillRegistry._names already excludes symlinked dirs; this is not the
# referenced-symlink case (see test_referenced_symlinked_skill_rejected below).


def test_symlinked_skill_dir_excluded_not_crash(tmp_path):
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    _write_effects(root, _EFFECTS)
    real = root / ".agents" / "skills" / "security-review"
    (root / ".agents" / "skills" / "sym-skill").symlink_to(real)
    result = _run(root)
    assert result.returncode == 0, (
        "unreferenced symlinked skill dir must be silently excluded, not cause a crash; "
        f"got nonzero.\nstdout={result.stdout}\nstderr={result.stderr}"
    )


# Case 9b-ref: skill that IS referenced in an allowlist but whose directory
# is a symlink must be rejected with UNSAFE_PATH.            [RED today]


def test_referenced_symlinked_skill_rejected(tmp_path):
    """A skill referenced by a constrained role whose dir is a symlink must fail."""
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    # Replace security-review dir (referenced by security-reviewer) with a symlink.
    skill_dir = root / ".agents" / "skills" / "security-review"
    target = root.parent / "sr-real"
    shutil.copytree(str(skill_dir), str(target))
    shutil.rmtree(str(skill_dir))
    skill_dir.symlink_to(target)
    _write_effects(root, _EFFECTS)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_UNSAFE_PATH)


# Case 9b-miss: skill referenced in constrained-role allowlist but SKILL.md
# absent from filesystem must fail.                          [RED today]


def test_referenced_skill_missing_from_filesystem_fails(tmp_path):
    """A skill in the allowlist with no SKILL.md on disk must fail."""
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    # Remove security-review from filesystem; security-reviewer still references it.
    shutil.rmtree(str(root / ".agents" / "skills" / "security-review"))
    _write_effects(root, _EFFECTS)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_MISSING_FILE)


# Case 9c: symlinked role profile rejected                        [RED today]


def test_symlinked_role_profile_rejected(tmp_path):
    root = _base_fixture(tmp_path)
    agents_dir = root / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    real = root / ".agents" / "skills" / "security-review" / "SKILL.md"
    (agents_dir / "security-reviewer.agent.md").symlink_to(real)
    (agents_dir / "operations-observer.agent.md").write_text(_READONLY_PROFILE)
    _write_effects(root, _EFFECTS)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_UNSAFE_PATH)


# Case 9d: unsafe path traversal in role profile field            [RED today]


def test_unsafe_profile_path_traversal_fails(tmp_path):
    """A profile path containing .. in skill-effects.json must fail."""
    root = _base_fixture(tmp_path)
    effects = copy.deepcopy(_EFFECTS)
    effects["roles"]["operations-observer"]["profile"] = "../../../etc/hosts"
    _write_observer_and_reviewer(root)
    _write_effects(root, effects)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_UNSAFE_PATH)


# ---------------------------------------------------------------------------
# D1 additions: missing required role metadata; forbidden effect grant
# ---------------------------------------------------------------------------


def test_missing_required_role_metadata_fails(tmp_path):
    """A required (optional=False) role absent from the roles map must fail."""
    root = _base_fixture(tmp_path)
    effects = copy.deepcopy(_EFFECTS)
    del effects["roles"]["operations-observer"]  # required role omitted
    _write_observer_and_reviewer(root)
    _write_effects(root, effects)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_MISSING_ROLE_META)


def test_weakened_constrained_role_effects_fails(tmp_path):
    """Constrained role metadata granting edit violates the read-only invariant."""
    root = _base_fixture(tmp_path)
    effects = copy.deepcopy(_EFFECTS)
    effects["roles"]["operations-observer"]["allowed_effects"] = ["read", "edit"]
    _write_observer_and_reviewer(root)
    _write_effects(root, effects)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_SCHEMA_ERROR)


# ---------------------------------------------------------------------------
# Case 10a: dynamic registry assignment fails                     [RED today]
# AST-based parser must reject mutation of ROLE_SKILL_ALLOWLIST after literal.
# ---------------------------------------------------------------------------


def test_dynamic_registry_assignment_fails(tmp_path):
    root = _base_fixture(tmp_path)
    reg = root / "agent_skills" / "registry.py"
    reg.write_text(reg.read_text() + '\nROLE_SKILL_ALLOWLIST["extra"] = {"security-review"}\n')
    _write_observer_and_reviewer(root)
    _write_effects(root, _EFFECTS)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_REGISTRY_ERROR)


# Case 10b: duplicate registry assignment fails                   [RED today]


def test_duplicate_registry_assignment_fails(tmp_path):
    root = _base_fixture(tmp_path)
    reg = root / "agent_skills" / "registry.py"
    reg.write_text(
        reg.read_text() + '\nROLE_SKILL_ALLOWLIST = {"duplicate": {"security-review"}}\n'
    )
    _write_observer_and_reviewer(root)
    _write_effects(root, _EFFECTS)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_REGISTRY_ERROR)


# Case 10c: duplicate JSON keys in skill-effects.json fails       [RED today]


def test_duplicate_json_keys_in_effects_fails(tmp_path):
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    dst = root / "docs" / "agents"
    dst.mkdir(parents=True, exist_ok=True)
    # Build a complete valid JSON from _EFFECTS, then inject one duplicate skill key.
    # json.dumps deduplicates; we inject manually so both values are independently valid.
    base_json = json.dumps(_EFFECTS)
    # base_json ends with }} (skills-close + root-close); insert duplicate before them.
    assert base_json.endswith("}}"), "_EFFECTS JSON must end with }} (skills-close + root-close)"
    duplicate_entry = ', "security-review": {"required_effects": ["read"]}'
    injected_json = base_json[:-2] + duplicate_entry + "}}"
    # Confirm the key appears exactly twice (original + injected); both values are ["read"].
    assert injected_json.count('"security-review"') == 2, (
        "expected exactly 2 occurrences of duplicate key in injected JSON"
    )
    (dst / "skill-effects.json").write_text(injected_json)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_SCHEMA_ERROR)


# ---------------------------------------------------------------------------
# Case 11: diagnostics are deterministic; checker makes no writes [GREEN today]
# ---------------------------------------------------------------------------


def test_deterministic_output_no_writes(tmp_path):
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    _write_effects(root, _EFFECTS)

    # Snapshot BEFORE first execution; exclude interpreter bytecode from scope.
    def _snapshot(path: Path) -> frozenset:
        return frozenset(
            (p, p.read_bytes())
            for p in path.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
        )

    before = _snapshot(root)
    r1 = _run(root)
    after_r1 = _snapshot(root)
    r2 = _run(root)
    after_r2 = _snapshot(root)

    assert r1.returncode == 0, (
        f"valid fixture must exit 0 on first run; got {r1.returncode}.\n"
        f"stdout={r1.stdout}\nstderr={r1.stderr}"
    )
    assert r2.returncode == 0, (
        f"valid fixture must exit 0 on second run; got {r2.returncode}.\n"
        f"stdout={r2.stdout}\nstderr={r2.stderr}"
    )
    assert r1.stdout == r2.stdout, "stdout must be identical across runs"
    assert r1.stderr == r2.stderr, "stderr must be identical across runs"

    assert before == after_r1, (
        "checker must not add, modify, or delete files on first run; "
        f"delta: {after_r1 ^ before}"
    )
    assert after_r1 == after_r2, (
        "checker must not add, modify, or delete files on second run; "
        f"delta: {after_r2 ^ after_r1}"
    )
