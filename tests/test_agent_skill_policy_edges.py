"""
Second supplemental behavioral contract tests — Groups B/C/D/E edge cases.
Contract: docs/agents/agent-policy-consistency-contract.md  Issue: #685
Second solution review finding: SOLUTION_CHANGES_REQUIRED — Groups B/C/D/E/global.

Eight cases covering AST mutation gaps (B1-B3), unconstrained-role filesystem
checks (E1-E2), constrained-role SKILL.md symlink (E3), YAML block-list with
inline comment (C), and required_effects type validation (D).

Uses ACTUAL public tests/validate_skills.py (cases 4,7,8) and standalone
scripts/check_agent_skill_policy.py --root (cases 1,2,3,5,6) against
controlled temporary repository fixtures.  Fixture helpers imported from
test_agent_skill_policy_consistency without editing that file.
No mock validators or checker logic in tests.

[RED today]  — 8 cases; becomes GREEN after Groups B/C/D/E/global fixes.
              Cases 1-3: AST walker misses Assign+Call and Subscript-receiver gaps.
              Cases 4-5: unconstrained-role skills not checked for dir existence/symlink.
              Case 6: constrained-role SKILL.md symlink not detected (dir is real).
              Case 7: YAML block-list inline comment suffix not stripped from token.
              Case 8: required_effects dict type not validated (key presence passes).

Accepted primary test blob 7cbabde0e9240a2fbfd093324f89741227aeda67 unchanged.
Accepted first supplement blob eb96fc5cfc71bf7fd4e6ced2d40f8a85974ed3b5 unchanged.
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
# Used for cases where public SkillRegistry.validate_all() would mask the
# targeted REGISTRY_ERROR / UNSAFE_PATH token before the policy checker runs.
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
# Profile constant — YAML block list with inline comment suffix.
# The comment is legal YAML; the token 'edit # forbidden mutation' must not
# silently pass as an unrecognised safe tool.
# ---------------------------------------------------------------------------

_BLOCK_COMMENT_EDIT_PROFILE = (
    "---\nname: Block-comment-edit\ntools:\n"
    "  - read\n"
    "  - edit # forbidden mutation\n"
    "---\nRead-only role with forbidden comment-suffixed tool.\n"
)

# ===========================================================================
# Case 1 (Group B1): result = ROLE_SKILL_ALLOWLIST.update(...) rejected  [RED today]
# Current: _read_allowlist() walks ast.Assign targets only; the Call on the
# RHS (the value attribute) is never inspected, so the mutation passes.
# Fix required: detect ast.Assign where value is a Call on ROLE_SKILL_ALLOWLIST.
# ===========================================================================


def test_allowlist_update_in_assign_rejected(tmp_path):
    """result = ROLE_SKILL_ALLOWLIST.update(...) must produce REGISTRY_ERROR.

    The update is appended after the literal dictionary so the literal remains
    parseable; only the assignment-form mutation is the targeted defect.
    docs-sync has a complete classification so that is not a confounding error.
    Standalone checker used; SkillRegistry NAME_RE does not mask REGISTRY_ERROR.
    """
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    _write_effects(root, _EFFECTS)
    reg = root / "agent_skills" / "registry.py"
    reg.write_text(
        reg.read_text()
        + "\nresult = ROLE_SKILL_ALLOWLIST.update({'operations-observer': {'docs-sync'}})\n"
    )
    result = _run_standalone(root)
    _assert_diagnostic(result, _TOKEN_REGISTRY_ERROR)


# ===========================================================================
# Case 2 (Group B2): removed = ROLE_SKILL_ALLOWLIST.pop(...) rejected    [RED today]
# Current: ast.Assign walker does not inspect Call values; pop() on the RHS
# passes unchecked just like update() in Case 1.
# Fix required: detect ast.Assign where value is a Call on ROLE_SKILL_ALLOWLIST.
# ===========================================================================


def test_allowlist_pop_in_assign_rejected(tmp_path):
    """removed = ROLE_SKILL_ALLOWLIST.pop('operations-observer') must produce REGISTRY_ERROR.

    Removing a role at import time mutates the allowlist; the assignment form
    must be caught the same as update().  Only this mutation is the targeted
    defect; all other metadata is valid.
    Standalone checker used; SkillRegistry NAME_RE does not mask REGISTRY_ERROR.
    """
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    _write_effects(root, _EFFECTS)
    reg = root / "agent_skills" / "registry.py"
    reg.write_text(
        reg.read_text() + "\nremoved = ROLE_SKILL_ALLOWLIST.pop('operations-observer')\n"
    )
    result = _run_standalone(root)
    _assert_diagnostic(result, _TOKEN_REGISTRY_ERROR)


# ===========================================================================
# Case 3 (Group B3): ROLE_SKILL_ALLOWLIST[role].add(...) standalone expr  [RED today]
# Current: ast.Expr(Call) branch requires call.func.value to be ast.Name with
# id == "ROLE_SKILL_ALLOWLIST"; a Subscript receiver is not ast.Name, so it
# passes through unchecked.
# Fix required: reject method calls on any expression rooted at ROLE_SKILL_ALLOWLIST.
# ===========================================================================


def test_allowlist_chained_subscript_add_rejected(tmp_path):
    """ROLE_SKILL_ALLOWLIST['operations-observer'].add('docs-sync') as standalone
    expression must produce REGISTRY_ERROR.

    This is NOT an assignment; it isolates the Subscript-receiver gap in the
    ast.Expr(Call) branch, distinct from Cases 1 and 2 which target the
    ast.Assign(Call) gap.
    docs-sync has a complete classification; only the mutation detection is targeted.
    Standalone checker used.
    """
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    _write_effects(root, _EFFECTS)
    reg = root / "agent_skills" / "registry.py"
    reg.write_text(
        reg.read_text() + "\nROLE_SKILL_ALLOWLIST['operations-observer'].add('docs-sync')\n"
    )
    result = _run_standalone(root)
    _assert_diagnostic(result, _TOKEN_REGISTRY_ERROR)


# ===========================================================================
# Case 4 (Global/E1): Unconstrained-role referenced skill dir absent     [RED today]
# Current: Step 4b validates classification but does not check SKILL.md
# existence for unconstrained roles; check is only in _check_skill() for
# constrained roles.  Removing docs-sync dir while team-lead/docs-engineer/
# alice still reference it exits 0.
# Fix required: verify SKILL.md existence for all allowlisted skills.
# ===========================================================================


def test_unconstrained_role_skill_dir_missing_fails(tmp_path):
    """Removing the docs-sync skill directory must produce MISSING_FILE.

    team-lead, docs-engineer, and alice all reference docs-sync in the fixture
    registry.  The classification entry is kept in skill-effects.json so that
    MISSING_FILE (filesystem check) is the only targeted defect, not
    MISSING_CLASSIFICATION.
    Constrained-role selections remain valid:
      operations-observer uses github-pr-readiness/github-ci-diagnosis/security-review;
      security-reviewer uses security-review.
    Public entrypoint used; SkillRegistry NAME_RE does not mask MISSING_FILE.
    """
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    _write_effects(root, _EFFECTS)
    docs_sync_dir = root / ".agents" / "skills" / "docs-sync"
    assert docs_sync_dir.is_dir(), "docs-sync skill directory missing from fixture"
    shutil.rmtree(docs_sync_dir)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_MISSING_FILE)


# ===========================================================================
# Case 5 (E2): Unconstrained-role skill dir replaced with symlink        [RED today]
# Current: symlink check is in _check_skill() for constrained roles only;
# Step 4b performs no symlink detection for unconstrained roles.
# Fix required: detect symlinked skill directories for all allowlisted skills.
# ===========================================================================


def test_unconstrained_role_skill_dir_symlink_rejected(tmp_path):
    """Replacing the docs-sync directory with a symlink must produce UNSAFE_PATH.

    A valid safe-target directory with an identical SKILL.md is created inside
    the fixture root so the symlink target exists and is readable; path traversal
    is not the issue — symlink use itself is the targeted defect.
    team-lead, docs-engineer, and alice reference docs-sync.
    Classification is kept so MISSING_CLASSIFICATION does not confound the result.
    Standalone checker used; public path through SkillRegistry may emit a
    different token before the policy checker handles the symlink.
    """
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    _write_effects(root, _EFFECTS)

    skills_root = root / ".agents" / "skills"
    docs_sync_dir = skills_root / "docs-sync"
    assert docs_sync_dir.is_dir(), "docs-sync skill directory missing from fixture"

    # Create a valid safe-target directory inside the fixture root.
    safe_target = skills_root / "safe-target"
    shutil.copytree(str(docs_sync_dir), str(safe_target))
    assert (safe_target / "SKILL.md").exists(), "safe-target SKILL.md not created"

    # Replace docs-sync directory with a symlink to safe-target.
    shutil.rmtree(docs_sync_dir)
    docs_sync_dir.symlink_to(safe_target)
    assert docs_sync_dir.is_symlink(), "symlink not created"
    assert (docs_sync_dir / "SKILL.md").exists(), "symlink target SKILL.md not reachable"

    result = _run_standalone(root)
    _assert_diagnostic(result, _TOKEN_UNSAFE_PATH)


# ===========================================================================
# Case 6 (E3): Constrained-role SKILL.md replaced with symlink          [RED today]
# Current: _check_skill() checks skill_dir.is_symlink() (directory level only).
# If the directory is real but SKILL.md is a symlink, skill_dir.is_symlink()
# returns False; (skill_dir / "SKILL.md").is_file() follows the link and
# returns True; no UNSAFE_PATH is emitted.
# Fix required: also check (skill_dir / "SKILL.md").is_symlink().
# ===========================================================================


def test_constrained_role_skill_md_symlink_rejected(tmp_path):
    """Replacing security-review/SKILL.md with a symlink must produce UNSAFE_PATH.

    The security-review directory itself is real; only the SKILL.md file is
    replaced by a symlink to a controlled safe.md inside the fixture root.
    The target file contains identical valid content so that the symlink is
    reachable; link existence is not the issue — the symlink itself is.
    Standalone checker used; the public path triggers a generic SkillFormatError
    from SkillRegistry rather than UNSAFE_PATH, which is not valid proof.
    """
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    _write_effects(root, _EFFECTS)

    skill_dir = root / ".agents" / "skills" / "security-review"
    assert skill_dir.is_dir(), "security-review skill directory missing from fixture"

    skill_md = skill_dir / "SKILL.md"
    assert skill_md.is_file(), "security-review/SKILL.md missing from fixture"

    # Create valid target file inside fixture root.
    safe_md = root / "safe-skill.md"
    shutil.copy2(skill_md, safe_md)
    assert safe_md.exists()

    # Replace SKILL.md with a symlink to safe-md.
    skill_md.unlink()
    skill_md.symlink_to(safe_md)
    assert skill_md.is_symlink(), "symlink not created"
    assert skill_md.is_file(), "symlink target not reachable"
    assert not skill_dir.is_symlink(), "directory must not be a symlink (isolate file-level gap)"

    result = _run_standalone(root)
    _assert_diagnostic(result, _TOKEN_UNSAFE_PATH)


# ===========================================================================
# Case 7 (Group C comment): YAML block list with inline comment rejected  [RED today]
# Current: block-list parser strips leading '- ' then strips quote chars; an
# inline comment ('# ...') is not stripped from the token, so 'edit # comment'
# is not in _MUTATION_TOOLS and the edit authority slips through.
# Fix required: strip or split at '#' for block-list items, or reject
# unsupported syntax fail-closed.
# ===========================================================================


def test_profile_tools_block_list_with_comment_rejected(tmp_path):
    """tools: block list with '- edit # comment' must produce POLICY_VIOLATION.

    The inline YAML comment after 'edit' is legal syntax; it does not make the
    declared authority unknown — 'edit' is still declared.  The fix must strip
    the comment suffix from the token before comparing with _MUTATION_TOOLS.
    Public entrypoint used.
    """
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    _write_profile(root, "operations-observer.agent.md", _BLOCK_COMMENT_EDIT_PROFILE)
    _write_effects(root, _EFFECTS)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_POLICY_VIOLATION)


# ===========================================================================
# Case 8 (Group D type): required_effects mapping instead of list         [RED today]
# Current: required_effects key presence is checked; iterating a dict yields
# keys, and 'read' in _KNOWN_EFFECTS passes; set({'read': True}) == {'read'}
# passes the subset check — no type validation.
# Fix required: assert isinstance(required_effects, list) or reject non-list type.
# ===========================================================================


def test_skill_required_effects_mapping_rejected(tmp_path):
    """security-review required_effects as {'read': true} (mapping) must produce SCHEMA_ERROR.

    All other classification fields and role profiles are valid so that the
    wrong field type is the only targeted defect.
    Public entrypoint used.
    """
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    effects = copy.deepcopy(_EFFECTS)
    effects["skills"]["security-review"]["required_effects"] = {"read": True}
    _write_effects(root, effects)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_SCHEMA_ERROR)
