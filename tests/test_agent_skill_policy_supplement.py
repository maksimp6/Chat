"""
Supplemental behavioral contract tests for agent-skill policy consistency.
Contract: docs/agents/agent-policy-consistency-contract.md  Issue: #685
Solution review finding: SOLUTION_CHANGES_REQUIRED — Groups A-E confirmed.

Nine cases covering five failure groups from the independent solution review.
Uses ACTUAL public tests/validate_skills.py (cases 1,2,4,5,6,7,9) and
standalone scripts/check_agent_skill_policy.py --root (cases 3,8) against
controlled temporary repository fixtures.  Fixture helpers imported from
test_agent_skill_policy_consistency without editing that file.
No mock validators or checker logic in tests.

[RED today]  — all 9 cases; current implementation exits 0 for all violations.
             Become GREEN after Groups A-E implementation fixes.

Accepted primary test blob 7cbabde0e9240a2fbfd093324f89741227aeda67 unchanged.
"""

import copy
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Repository root
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Import fixture helpers from the accepted primary test module without editing it.
# importlib.util used because pytest is not available outside the test runner.
# ---------------------------------------------------------------------------


def _load_primary() -> object:
    spec = importlib.util.spec_from_file_location(
        "_policy_consistency",
        _REPO / "tests" / "test_agent_skill_policy_consistency.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_C = _load_primary()

_base_fixture = _C._base_fixture
_patch_registry_allowlist = _C._patch_registry_allowlist
_write_effects = _C._write_effects
_write_profile = _C._write_profile
_write_observer_and_reviewer = _C._write_observer_and_reviewer
_run = _C._run
_assert_diagnostic = _C._assert_diagnostic
_EFFECTS = _C._EFFECTS
_READONLY_PROFILE = _C._READONLY_PROFILE

_TOKEN_SCHEMA_ERROR = _C._TOKEN_SCHEMA_ERROR
_TOKEN_POLICY_VIOLATION = _C._TOKEN_POLICY_VIOLATION
_TOKEN_REGISTRY_ERROR = _C._TOKEN_REGISTRY_ERROR
_TOKEN_UNSAFE_PATH = _C._TOKEN_UNSAFE_PATH
_TOKEN_MISSING_FILE = _C._TOKEN_MISSING_FILE
_TOKEN_MISSING_ROLE_META = _C._TOKEN_MISSING_ROLE_META
_TOKEN_MISSING_CLASS = _C._TOKEN_MISSING_CLASS

# ---------------------------------------------------------------------------
# Standalone checker runner.
# Used for cases where SkillRegistry.validate_all() NAME_RE validation would
# reject the targeted skill name before the policy checker runs (cases 3, 8).
# ---------------------------------------------------------------------------


def _run_standalone(tmp: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(tmp / "scripts" / "check_agent_skill_policy.py"),
            "--root",
            str(tmp),
        ],
        capture_output=True,
        text=True,
        cwd=str(tmp),
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Profile content constants for cases 4 and 5.
# ---------------------------------------------------------------------------

# Quoted JSON-style array containing an edit tool — valid YAML representation.
_QUOTED_EDIT_PROFILE = '---\nname: Quoted-edit\ntools: ["read","edit"]\n---\n'

# YAML block sequence containing an edit tool — valid declared mutation authority.
_BLOCK_EDIT_PROFILE = "---\nname: Block-edit\ntools:\n  - read\n  - edit\n---\n"


# ===========================================================================
# Case 1 (Group A2): Missing helper must fail-closed               [RED today]
# Current: if helper.exists(): ... silently skips the policy gate when absent.
# Fix required: require helper present; emit MISSING_FILE and exit nonzero.
# ===========================================================================


def test_missing_helper_fails_closed(tmp_path):
    """Absent scripts/check_agent_skill_policy.py must cause nonzero + MISSING_FILE.

    _base_fixture copies the helper when it exists.  The test removes it to
    verify that the public entrypoint is fail-closed, not fail-open.
    No other metadata is invalid.
    """
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    _write_effects(root, _EFFECTS)
    helper = root / "scripts" / "check_agent_skill_policy.py"
    assert helper.exists(), (
        "helper was not copied by _base_fixture — cannot test fail-closed behaviour"
    )
    helper.unlink()
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_MISSING_FILE)


# ===========================================================================
# Case 2 (Group A1): Policy gate must run before registry module import  [RED today]
# Current: validate_skills.py imports SkillRegistry at module load (before main),
# executing registry module-level code before the subprocess helper check.
# Fix required: call helper gate before any registry import or execution.
# ===========================================================================


def test_gate_before_registry_import(tmp_path):
    """Registry module-level code must not execute before SCHEMA_ERROR is raised.

    The fixture registry.py writes a sentinel file at module-import time.
    A control subprocess proves the sentinel would be written if registry is imported.
    Running the public CLI with version=99 must give SCHEMA_ERROR + sentinel absent.
    """
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    effects = copy.deepcopy(_EFFECTS)
    effects["version"] = 99
    _write_effects(root, effects)

    sentinel = root / "registry_imported.marker"
    marker_line = f"open({str(sentinel)!r}, 'w').close()  # import-time sentinel\n"
    reg = root / "agent_skills" / "registry.py"
    original = reg.read_text()
    reg.write_text(marker_line + original)

    # Control: prove that importing the fixture registry.py writes the sentinel.
    # Uses a separate control root so the main fixture sentinel is not pre-written.
    ctl_root = tmp_path / "_ctl"
    ctl_root.mkdir()
    ctl_sentinel = ctl_root / "ctl.marker"
    shutil.copytree(str(root / "agent_skills"), str(ctl_root / "agent_skills"))
    ctl_reg = ctl_root / "agent_skills" / "registry.py"
    ctl_marker = f"open({str(ctl_sentinel)!r}, 'w').close()  # ctl sentinel\n"
    ctl_reg.write_text(ctl_marker + original)
    ctl = subprocess.run(
        [
            sys.executable,
            "-c",
            (f"import sys; sys.path.insert(0, {str(ctl_root)!r}); import agent_skills.registry"),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert ctl.returncode == 0, (
        f"control import failed unexpectedly — fixture injection invalid: {ctl.stderr!r}"
    )
    assert ctl_sentinel.exists(), (
        "control sentinel not written — registry.py injection is invalid; "
        "cannot assert gate ordering"
    )

    # Main assertion: public CLI must emit SCHEMA_ERROR and sentinel must be absent.
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_SCHEMA_ERROR)
    assert not sentinel.exists(), (
        "registry_imported.marker was written — policy gate ran AFTER registry module import.\n"
        "Gate must execute before any registry module-level code.\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


# ===========================================================================
# Case 3 (Group B): ROLE_SKILL_ALLOWLIST.update() must be rejected  [RED today]
# Current: AST walker covers Assign and AugAssign but not Expr(Call) mutations.
# Standalone checker used; public CLI via SkillRegistry validate_all would not
# expose the REGISTRY_ERROR token (SkillRegistry name-checks mask it).
# Fix required: detect and reject method-call mutations on ROLE_SKILL_ALLOWLIST.
# ===========================================================================


def test_allowlist_update_method_rejected(tmp_path):
    """ROLE_SKILL_ALLOWLIST.update(...) after the literal must produce REGISTRY_ERROR.

    docs-sync is a valid skill with complete classification so that the mutation
    is the only targeted violation.
    """
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    _write_effects(root, _EFFECTS)
    reg = root / "agent_skills" / "registry.py"
    reg.write_text(
        reg.read_text() + "\nROLE_SKILL_ALLOWLIST.update({'operations-observer': {'docs-sync'}})\n"
    )
    result = _run_standalone(root)
    _assert_diagnostic(result, _TOKEN_REGISTRY_ERROR)


# ===========================================================================
# Case 4 (Group C): Profile quoted-array tools with edit rejected  [RED today]
# Current: _parse_profile_tools strips '[' and ']' then splits on ','; tokens
# retain surrounding quotes, so '"edit"' is not in _MUTATION_TOOLS.
# Fix required: strip quote characters from tokens or parse all YAML formats.
# ===========================================================================


def test_profile_tools_quoted_array_rejected(tmp_path):
    """tools: [\"read\",\"edit\"] in frontmatter (quoted JSON-style) must produce POLICY_VIOLATION.

    This is a valid YAML representation of the same forbidden mutation authority
    as tools: [read, edit]; it must not pass the profile check.
    """
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    _write_profile(root, "operations-observer.agent.md", _QUOTED_EDIT_PROFILE)
    _write_effects(root, _EFFECTS)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_POLICY_VIOLATION)


# ===========================================================================
# Case 5 (Group C): Profile YAML block-list tools with edit rejected  [RED today]
# Current: _parse_profile_tools returns [] when tools: value is empty (block seq).
# Fix required: parse YAML block sequences or reject unsupported formats fail-closed.
# ===========================================================================


def test_profile_tools_yaml_block_list_rejected(tmp_path):
    """tools: YAML block sequence containing edit must produce POLICY_VIOLATION.

    This is valid declared mutation authority, not a malformed frontmatter error.
    The fix must reject the authority, not treat a block list as empty tools.
    """
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    _write_profile(root, "operations-observer.agent.md", _BLOCK_EDIT_PROFILE)
    _write_effects(root, _EFFECTS)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_POLICY_VIOLATION)


# ===========================================================================
# Case 6 (Group D1): Empty skill classification {} must fail         [RED today]
# Current: skills_data[skill].get("required_effects", []) defaults to [];
# empty set is always a subset of allowed_effects -> exits 0.
# Fix required: require required_effects key; emit SCHEMA_ERROR if absent.
# ===========================================================================


def test_empty_skill_classification_fails(tmp_path):
    """security-review: {} (missing required_effects key) must produce SCHEMA_ERROR.

    security-reviewer references security-review; the missing key must be caught
    as a schema invariant violation, not silently treated as read-only.
    """
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    effects = copy.deepcopy(_EFFECTS)
    effects["skills"]["security-review"] = {}
    _write_effects(root, effects)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_SCHEMA_ERROR)


# ===========================================================================
# Case 7 (Group D2): Optional role on both surfaces, absent from roles map  [RED today]
# Current: step 3 continues for optional role absent from roles_data (no error);
# step 5 falls back to default metadata -> exits 0 without MISSING_ROLE_METADATA.
# Fix required: any role present on either surface must have a roles_data entry.
# ===========================================================================


def test_optional_role_present_missing_metadata_fails(tmp_path):
    """WC in allowlist and profile file present, but absent from roles map.

    Must produce MISSING_ROLE_METADATA.  Optional means absent-from-both-surfaces
    only; when present on both surfaces the role must be validated with metadata.

    Existing absent-both (NOT_PRESENT) and one-sided cases in the primary test
    file remain unaffected — they use separate fixture configurations.
    """
    root = _base_fixture(tmp_path)
    _patch_registry_allowlist(root, "work-coordinator", {"security-review"})
    _write_profile(root, "work-coordinator.agent.md", _READONLY_PROFILE)
    _write_observer_and_reviewer(root)
    effects = copy.deepcopy(_EFFECTS)
    del effects["roles"]["work-coordinator"]
    _write_effects(root, effects)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_MISSING_ROLE_META)


# ===========================================================================
# Case 8 (Group E): Skill name path traversal must be rejected      [RED today]
# Current: _check_skill builds skill_dir = skills_root / skill without
# validating traversal components; ../../outside resolves outside .agents/skills/.
# Standalone checker used; SkillRegistry NAME_RE rejects the name first via
# validate_all(), masking the UNSAFE_PATH token from the policy checker.
# Fix required: validate skill name for .. components or absolute paths.
# ===========================================================================


def test_skill_name_path_traversal_rejected(tmp_path):
    """../../outside as skill name in observer allowlist must produce UNSAFE_PATH.

    A matching read classification and a SKILL.md outside .agents/skills/ are
    created so that path traversal detection is the only targeted defect.
    Both duplicate values are read-only; policy violation is not the target here.
    """
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    _patch_registry_allowlist(
        root,
        "operations-observer",
        {"github-pr-readiness", "github-ci-diagnosis", "security-review", "../../outside"},
    )
    outside_dir = root / "outside"
    outside_dir.mkdir()
    (outside_dir / "SKILL.md").write_text("# outside skill\n")
    effects = copy.deepcopy(_EFFECTS)
    effects["skills"]["../../outside"] = {"required_effects": ["read"]}
    _write_effects(root, effects)
    result = _run_standalone(root)
    _assert_diagnostic(result, _TOKEN_UNSAFE_PATH)


# ===========================================================================
# Case 9: Every referenced skill must have a classification          [RED today]
# Original contract: "For each referenced skill require a classification."
# Current: checker validates only skills of the 3 constrained roles; unconstrained
# roles (team-lead, docs-engineer, alice) referencing docs-sync are not checked.
# Fix required: validate classification for all skills in all allowlisted roles.
# Presented for independent contract amendment acceptance before implementation.
# ===========================================================================


def test_referenced_skill_missing_classification_for_unconstrained_role(tmp_path):
    """Removing docs-sync classification while team-lead/docs-engineer/alice
    reference it must produce MISSING_CLASSIFICATION.

    Constrained-role selections remain fully classified: operations-observer uses
    github-pr-readiness/github-ci-diagnosis/security-review (all read); security-
    reviewer uses security-review (read).  Current checker exits 0 because it
    only checks constrained-role skills.  After fix, all referenced skills
    validated regardless of role type.
    """
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    effects = copy.deepcopy(_EFFECTS)
    del effects["skills"]["docs-sync"]
    _write_effects(root, effects)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_MISSING_CLASS)
