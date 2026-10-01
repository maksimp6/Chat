"""
Third supplemental behavioral contract tests — schema field types and read-method safety.
Contract: docs/agents/agent-policy-consistency-contract.md  Issue: #685
Third solution review finding: SOLUTION_CHANGES_REQUIRED — Issues 2, 3, 4.

Three cases covering missing required role field (Issue 2), unsupported nested
tool-list structure (Issue 3), and a positive contract guard confirming that a
legitimate registry .get() call is not mistakenly flagged (Issue 4).

Uses ACTUAL public tests/validate_skills.py (cases 1 and 2) and standalone
scripts/check_agent_skill_policy.py --root (case 3) against controlled
temporary repository fixtures.  Fixture helpers imported from
test_agent_skill_policy_consistency without editing that file.
No mock validators or checker logic in tests.

[RED today]  — 3 cases; becomes GREEN after Issues 2/3/4 implementation fixes.
              Case 1: roles.security-reviewer.allowed_effects absent; Step 3
              defaults [] and Step 5 defaults ["read"], silently granting
              read authorization to incomplete metadata.
              Case 2: tools: [[read], [edit]] produces tokens '[read]'/'[edit]',
              which are not in _MUTATION_TOOLS, so the profile passes undetected.
              Case 3 (positive): result = ROLE_SKILL_ALLOWLIST.get(...) is a
              legitimate read; current _read_allowlist blanket Assign-RHS rejection
              wrongly emits REGISTRY_ERROR and exits 1.

Accepted primary test blob 7cbabde0e9240a2fbfd093324f89741227aeda67 unchanged.
Accepted first supplement blob eb96fc5cfc71bf7fd4e6ced2d40f8a85974ed3b5 unchanged.
Accepted second supplement (edges) blob f0e74d2667df216b79332daefe3a15978828044a unchanged.
"""

import copy
import importlib.util
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
_TOKEN_REGISTRY_ERROR = _C._TOKEN_REGISTRY_ERROR

# ---------------------------------------------------------------------------
# Standalone checker runner.
# Used for Case 3 where the public entrypoint registry import would mask the
# REGISTRY_ERROR token at module-import time.
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
# Profile constant — nested list tool value; unsupported structure that must
# be rejected fail-closed rather than silently accepted as unknown tokens.
# ---------------------------------------------------------------------------

_NESTED_LIST_PROFILE = "---\nname: Nested-list\ntools: [[read], [edit]]\n---\nUnsupported nested list.\n"


# ===========================================================================
# Case 1 (Issue 2): required role field allowed_effects absent  [RED today]
# Current: roles.security-reviewer.allowed_effects absent; Step 3 defaults []
# (no error), and Step 5 defaults ["read"] (silently authorizes read).
# Fix required: require allowed_effects key present in constrained role entry;
# emit SCHEMA_ERROR when missing.
# ===========================================================================


def test_constrained_role_allowed_effects_field_absent_fails(tmp_path):
    """Delete only roles.security-reviewer.allowed_effects; all other fields intact.

    Profile present; optional=False field present; skills data valid.
    The only targeted invariant is the missing required allowed_effects field.
    Public CLI => nonzero + SCHEMA_ERROR; currently exits 0.
    """
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    effects = copy.deepcopy(_EFFECTS)
    # Delete only the allowed_effects field; retain profile and optional fields.
    del effects["roles"]["security-reviewer"]["allowed_effects"]
    _write_effects(root, effects)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_SCHEMA_ERROR)


# ===========================================================================
# Case 2 (Issue 3): tools: [[read], [edit]] nested-list rejected  [RED today]
# Current: val.startswith("[") path splits on "," producing tokens '[read]' and
# '[edit]'; neither is in _MUTATION_TOOLS so the profile passes undetected.
# Fix required: validate that each token is a plain identifier; nested lists
# (tokens containing brackets) must emit SCHEMA_ERROR (unsupported form).
# ===========================================================================


def test_profile_tools_nested_list_rejected(tmp_path):
    """observer profile frontmatter tools: [[read], [edit]] must emit SCHEMA_ERROR.

    This is an unsupported nested tool-value structure; the targeted defect is
    the wrong field type, not a declared mutation, so SCHEMA_ERROR is correct
    (not POLICY_VIOLATION).  Otherwise-valid fixture.
    Public CLI => nonzero + SCHEMA_ERROR; currently exits 0.
    """
    root = _base_fixture(tmp_path)
    _write_profile(root, "operations-observer.agent.md", _NESTED_LIST_PROFILE)
    _write_profile(root, "security-reviewer.agent.md", _READONLY_PROFILE)
    _write_effects(root, _EFFECTS)
    result = _run(root)
    _assert_diagnostic(result, _TOKEN_SCHEMA_ERROR)


# ===========================================================================
# Case 3 (Issue 4 — positive): .get() read method must NOT be REGISTRY_ERROR  [RED today]
# Current: _read_allowlist() rejects any ast.Assign where the RHS is a Call
# attributed to ROLE_SKILL_ALLOWLIST, including pure read methods like .get().
# Fix required: only reject known mutation methods; .get() and similar reads
# must pass.  Standalone used because the public path imports registry at
# module level, masking the checker result.
# ===========================================================================


def test_allowlist_read_method_permitted(tmp_path):
    """Append result = ROLE_SKILL_ALLOWLIST.get('operations-observer') after registry source.

    No mutation occurs.  The standalone checker must exit 0 — no REGISTRY_ERROR.
    This is a positive contract test: it is currently RED because the checker
    wrongly returns 1 + REGISTRY_ERROR for a benign read assignment.

    First a baseline control confirms that an otherwise-identical fixture with
    NO appended assignment exits 0 (verifying the fixture is independently valid).
    Then the appended .get() assignment is proved to be the sole cause of failure.
    """
    root = _base_fixture(tmp_path)
    _write_observer_and_reviewer(root)
    _write_effects(root, _EFFECTS)

    # Baseline control: no appended assignment — must exit 0.
    control = _run_standalone(root)
    assert control.returncode == 0, (
        f"baseline fixture must exit 0; got {control.returncode}.\n"
        f"stdout={control.stdout}\nstderr={control.stderr}"
    )

    # Append a pure read assignment after actual registry source.
    reg = root / "agent_skills" / "registry.py"
    original = reg.read_text()
    read_line = "\nresult = ROLE_SKILL_ALLOWLIST.get('operations-observer')\n"
    reg.write_text(original + read_line)

    result = _run_standalone(root)
    assert result.returncode == 0, (
        f"legitimate .get() read method must NOT produce REGISTRY_ERROR.\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
