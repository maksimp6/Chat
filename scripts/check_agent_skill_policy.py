#!/usr/bin/env python3
"""Agent-skill policy consistency checker.

Stdlib only. Reads agent_skills/registry.py via safe AST literal analysis,
docs/agents/skill-effects.json via duplicate-key-rejecting JSON load, and
role profile files via frontmatter parsing. Emits one bounded diagnostic
token per failure to stderr. Never writes files, accesses network, or imports
agent_skills at runtime.

Usage:
    python scripts/check_agent_skill_policy.py [--root REPO_ROOT]

Exit code 0 on success; 1 on any policy, schema, registry, or path failure.
"""

import argparse
import ast
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Known values and constants
# ---------------------------------------------------------------------------

_KNOWN_EFFECTS = frozenset({"read", "edit", "deploy", "merge", "permissions"})

# Constrained roles: name -> is_optional.
# Hardcoded from the contract; JSON metadata confirms classification but cannot
# override these boundaries.
_CONSTRAINED_ROLES: dict = {
    "work-coordinator": True,
    "operations-observer": False,
    "security-reviewer": False,
}

# Tools forbidden on constrained read-only role profiles.
_MUTATION_TOOLS = frozenset({"edit", "write", "merge", "deploy"})

# Mutating methods on the literal dict and its set values. Pure reads such as
# get(), keys(), and copy() do not change the allowlist.
_ALLOWLIST_MUTATORS = frozenset(
    {
        "update",
        "pop",
        "popitem",
        "setdefault",
        "clear",
        "add",
        "remove",
        "discard",
        "difference_update",
        "intersection_update",
        "symmetric_difference_update",
        "__setitem__",
        "__delitem__",
        "__ior__",
        "__iand__",
        "__isub__",
        "__ixor__",
    }
)


# ---------------------------------------------------------------------------
# Diagnostic exception
# ---------------------------------------------------------------------------


class _PolicyError(Exception):
    """Policy or structural failure; carries a stable diagnostic token."""

    def __init__(self, token: str, msg: str) -> None:
        super().__init__(msg)
        self.token = token
        self.msg = msg


# ---------------------------------------------------------------------------
# JSON loading with duplicate-key detection
# ---------------------------------------------------------------------------


def _load_effects_json(path: Path) -> dict:
    if not path.exists():
        raise _PolicyError("MISSING_FILE", "docs/agents/skill-effects.json not found")
    text = path.read_text(encoding="utf-8")

    def _pairs(pairs: list) -> dict:
        result: dict = {}
        for k, v in pairs:
            if k in result:
                raise _PolicyError(
                    "SCHEMA_ERROR",
                    f"duplicate JSON key in skill-effects.json: {k!r}",
                )
            result[k] = v
        return result

    try:
        return json.loads(text, object_pairs_hook=_pairs)
    except _PolicyError:
        raise
    except Exception as exc:
        raise _PolicyError("SCHEMA_ERROR", f"skill-effects.json parse error: {exc}") from exc


# ---------------------------------------------------------------------------
# AST helpers for allowlist mutation detection
# ---------------------------------------------------------------------------


def _rooted_at_allowlist(node: ast.expr) -> bool:
    """Return True if node is ROLE_SKILL_ALLOWLIST or a subscript/attribute chain rooted there.

    This follows direct subscript/attribute receivers, not aliases or call results.
    """
    if isinstance(node, ast.Name):
        return node.id == "ROLE_SKILL_ALLOWLIST"
    if isinstance(node, ast.Subscript):
        return _rooted_at_allowlist(node.value)
    if isinstance(node, ast.Attribute):
        return _rooted_at_allowlist(node.value)
    return False


# ---------------------------------------------------------------------------
# Registry allowlist reading via safe AST analysis
# ---------------------------------------------------------------------------


def _read_allowlist(root: Path) -> dict:
    """Return {role: {skill, ...}} from ROLE_SKILL_ALLOWLIST in registry.py.

    Fails closed on syntax errors, dynamic mutations, duplicate assignments,
    or non-literal values.  Does not import or execute registry.py.
    """
    reg_path = root / "agent_skills" / "registry.py"
    if not reg_path.exists():
        raise _PolicyError("MISSING_FILE", "agent_skills/registry.py not found")

    source = reg_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(reg_path))
    except SyntaxError as exc:
        raise _PolicyError("REGISTRY_ERROR", f"registry.py syntax error: {exc}") from exc

    assignments: list = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "ROLE_SKILL_ALLOWLIST":
                    assignments.append(node)
                elif _rooted_at_allowlist(target) and not (
                    isinstance(target, ast.Name) and target.id == "ROLE_SKILL_ALLOWLIST"
                ):
                    # Subscript or attribute assignment rooted at allowlist
                    raise _PolicyError(
                        "REGISTRY_ERROR",
                        "ROLE_SKILL_ALLOWLIST has a dynamic subscript/attribute assignment",
                    )
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "ROLE_SKILL_ALLOWLIST":
                raise _PolicyError(
                    "REGISTRY_ERROR",
                    "ROLE_SKILL_ALLOWLIST has an augmented assignment",
                )
            if _rooted_at_allowlist(node.target):
                raise _PolicyError(
                    "REGISTRY_ERROR",
                    "ROLE_SKILL_ALLOWLIST has an augmented assignment on a derived target",
                )
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                if _rooted_at_allowlist(target):
                    raise _PolicyError(
                        "REGISTRY_ERROR",
                        "ROLE_SKILL_ALLOWLIST has a delete statement",
                    )
        elif isinstance(node, ast.Call):
            # ast.walk reaches calls in assignments, wrappers, and other expression
            # contexts. Reject mutations, while allowing direct rooted read methods.
            if (
                isinstance(node.func, ast.Attribute)
                and _rooted_at_allowlist(node.func.value)
                and node.func.attr in _ALLOWLIST_MUTATORS
            ):
                raise _PolicyError(
                    "REGISTRY_ERROR",
                    f"ROLE_SKILL_ALLOWLIST has a mutating method call: .{node.func.attr}()",
                )

    if not assignments:
        raise _PolicyError("REGISTRY_ERROR", "ROLE_SKILL_ALLOWLIST not found in registry.py")
    if len(assignments) > 1:
        raise _PolicyError(
            "REGISTRY_ERROR",
            "ROLE_SKILL_ALLOWLIST assigned more than once in registry.py",
        )

    assign_node = assignments[0]

    # Reject duplicate literal string keys in the dict node before literal_eval
    # can silently collapse them.
    if isinstance(assign_node.value, ast.Dict):
        seen: list = []
        for key_node in assign_node.value.keys:
            if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                if key_node.value in seen:
                    raise _PolicyError(
                        "REGISTRY_ERROR",
                        f"ROLE_SKILL_ALLOWLIST has duplicate literal key: {key_node.value!r}",
                    )
                seen.append(key_node.value)

    try:
        raw = ast.literal_eval(assign_node.value)
    except Exception as exc:
        raise _PolicyError(
            "REGISTRY_ERROR",
            f"ROLE_SKILL_ALLOWLIST is not a safe literal: {exc}",
        ) from exc

    if not isinstance(raw, dict):
        raise _PolicyError("REGISTRY_ERROR", "ROLE_SKILL_ALLOWLIST must be a dict literal")

    result: dict = {}
    for role_key, skill_val in raw.items():
        if not isinstance(role_key, str):
            raise _PolicyError("REGISTRY_ERROR", f"role key is not a string: {role_key!r}")
        if not isinstance(skill_val, (set, frozenset)):
            raise _PolicyError(
                "REGISTRY_ERROR",
                f"skill value for role {role_key!r} is not a set literal",
            )
        for s in skill_val:
            if not isinstance(s, str):
                raise _PolicyError(
                    "REGISTRY_ERROR",
                    f"skill name not a string in role {role_key!r}: {s!r}",
                )
        result[role_key] = set(skill_val)

    return result


# ---------------------------------------------------------------------------
# Profile path safety check
# ---------------------------------------------------------------------------


def _check_profile_path_safe(root: Path, profile_rel: str, role: str) -> Path:
    """Validate profile path from metadata for safety.  Returns the Path object."""
    p = Path(profile_rel)
    if p.is_absolute() or ".." in p.parts:
        raise _PolicyError(
            "UNSAFE_PATH",
            f"role {role!r} profile path is unsafe (absolute or path traversal): {profile_rel!r}",
        )
    abs_p = (root / profile_rel).resolve()
    try:
        abs_p.relative_to(root)
    except ValueError:
        raise _PolicyError(
            "UNSAFE_PATH",
            f"role {role!r} profile path escapes repository root: {profile_rel!r}",
        )
    return root / profile_rel


# ---------------------------------------------------------------------------
# Profile frontmatter tool parsing
# ---------------------------------------------------------------------------


def _strip_tools_comment(value: str) -> str:
    """Strip a YAML comment outside quotes in the supported tools grammar."""
    quote = None
    for i, char in enumerate(value):
        if quote is not None:
            if char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char == "#":
            return value[:i].strip()
    return value.strip()


def _parse_tool_scalar(value: str, profile_path: Path) -> str:
    """Accept one plain identifier, optionally enclosed in matching quotes."""
    value = value.strip()
    if value.startswith(('"', "'")):
        if len(value) < 2 or value[-1] != value[0]:
            raise _PolicyError(
                "SCHEMA_ERROR",
                f"profile {profile_path.name!r}: malformed quoted tool name",
            )
        value = value[1:-1]
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", value) is None:
        raise _PolicyError(
            "SCHEMA_ERROR",
            f"profile {profile_path.name!r}: tools must contain flat scalar identifiers",
        )
    return value


def _parse_profile_tools(profile_path: Path) -> list:
    """Return tool names from YAML frontmatter 'tools:'.

    Handles flat inline arrays ([read, search], ["read","edit"]) and
    YAML block sequences (- item lines), with comments outside quotes.
    Unknown/unsupported formats fail closed with SCHEMA_ERROR.
    Returns [] for an empty inline array or no 'tools:' key in the frontmatter.
    """
    text = profile_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    fm_lines: list = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        fm_lines.append(line)

    for i, line in enumerate(fm_lines):
        stripped = line.strip()
        if not stripped.startswith("tools:"):
            continue
        val = _strip_tools_comment(stripped[len("tools:") :])

        if val.startswith("["):
            if not val.endswith("]"):
                raise _PolicyError(
                    "SCHEMA_ERROR",
                    f"profile {profile_path.name!r}: malformed tools inline array: {val!r}",
                )
            inner = val[1:-1]
            if not inner.strip():
                return []
            return [_parse_tool_scalar(item, profile_path) for item in inner.split(",")]

        if val == "":
            # YAML block sequence: collect "- item" lines that follow
            tools: list = []
            item_indent = None
            key_indent = len(line) - len(line.lstrip())
            for j in range(i + 1, len(fm_lines)):
                item_line = fm_lines[j]
                item_stripped = _strip_tools_comment(item_line)
                if not item_stripped:
                    continue
                indent = len(item_line) - len(item_line.lstrip())
                if item_stripped.startswith("- ") or item_stripped == "-":
                    if item_indent is None:
                        item_indent = indent
                    elif indent != item_indent:
                        raise _PolicyError(
                            "SCHEMA_ERROR",
                            f"profile {profile_path.name!r}: nested tools block sequence",
                        )
                    tools.append(_parse_tool_scalar(item_stripped[1:], profile_path))
                elif indent > key_indent:
                    raise _PolicyError(
                        "SCHEMA_ERROR",
                        f"profile {profile_path.name!r}: unsupported tools block structure",
                    )
                else:
                    break
            if tools:
                return tools
            raise _PolicyError(
                "SCHEMA_ERROR",
                f"profile {profile_path.name!r}: 'tools:' block sequence is empty or unreadable",
            )

        raise _PolicyError(
            "SCHEMA_ERROR",
            f"profile {profile_path.name!r}: unsupported 'tools:' value format: {val!r}",
        )

    return []


# ---------------------------------------------------------------------------
# Unified skill filesystem validator (used for all roles)
# ---------------------------------------------------------------------------


def _validate_skill_path_and_fs(root: Path, skill: str, role: str) -> None:
    """Validate skill name safety and SKILL.md integrity for any role.

    Checks: no absolute/traversal path, skill dir exists and is not a symlink,
    SKILL.md exists and is not a symlink.  Order matters: symlink checks come
    before is_file/is_dir so we never follow a symlink before rejecting it.
    """
    skill_path_obj = Path(skill)
    if skill_path_obj.is_absolute() or ".." in skill_path_obj.parts:
        raise _PolicyError(
            "UNSAFE_PATH",
            f"role {role!r}: skill name {skill!r} contains unsafe path components",
        )
    skills_root = root / ".agents" / "skills"
    skill_dir = skills_root / skill
    if skill_dir.is_symlink():
        raise _PolicyError(
            "UNSAFE_PATH",
            f"role {role!r}: skill {skill!r} directory is a symlink",
        )
    if not skill_dir.is_dir():
        raise _PolicyError(
            "MISSING_FILE",
            f"role {role!r}: skill {skill!r} directory not found on filesystem",
        )
    skill_md = skill_dir / "SKILL.md"
    if skill_md.is_symlink():
        raise _PolicyError(
            "UNSAFE_PATH",
            f"role {role!r}: skill {skill!r} SKILL.md is a symlink",
        )
    if not skill_md.is_file():
        raise _PolicyError(
            "MISSING_FILE",
            f"role {role!r}: skill {skill!r} has no SKILL.md on filesystem",
        )


# ---------------------------------------------------------------------------
# Per-skill check for a constrained role (effects subset + filesystem)
# ---------------------------------------------------------------------------


def _check_skill(
    root: Path,
    skill: str,
    role: str,
    allowed_effects: list,
    skills_data: dict,
) -> None:
    if skill not in skills_data:
        raise _PolicyError(
            "MISSING_CLASSIFICATION",
            f"role {role!r}: skill {skill!r} has no entry in skill-effects.json",
        )
    _validate_skill_path_and_fs(root, skill, role)
    required = set(skills_data[skill]["required_effects"])
    allowed = set(allowed_effects)
    if not required.issubset(allowed):
        excess = sorted(required - allowed)
        raise _PolicyError(
            "POLICY_VIOLATION",
            f"role {role!r} + skill {skill!r}: requires effects {excess!r} "
            f"not in allowed_effects {sorted(allowed)!r}",
        )


# ---------------------------------------------------------------------------
# Main check logic
# ---------------------------------------------------------------------------


def _run_checks(root: Path) -> None:
    """Run all policy checks.  Raises _PolicyError on the first failure."""
    effects_path = root / "docs" / "agents" / "skill-effects.json"

    # Step 1: Load and parse skill-effects.json with duplicate-key detection.
    effects_data = _load_effects_json(effects_path)
    if not isinstance(effects_data, dict):
        raise _PolicyError("SCHEMA_ERROR", "skill-effects.json must be a JSON object")

    # Step 2a: Validate version.
    if type(effects_data.get("version")) is not int or effects_data["version"] != 1:
        raise _PolicyError(
            "SCHEMA_ERROR",
            f"unsupported skill-effects.json version: {effects_data.get('version')!r}",
        )

    # Step 2b: Validate top-level structure.
    roles_data = effects_data.get("roles")
    skills_data = effects_data.get("skills")
    if not isinstance(roles_data, dict):
        raise _PolicyError("SCHEMA_ERROR", "skill-effects.json missing or invalid 'roles' map")
    if not isinstance(skills_data, dict):
        raise _PolicyError("SCHEMA_ERROR", "skill-effects.json missing or invalid 'skills' map")

    # Step 2c: Validate skill classification entries (type + required fields + known effects).
    for skill_name, skill_info in skills_data.items():
        if not isinstance(skill_info, dict):
            raise _PolicyError(
                "SCHEMA_ERROR",
                f"skill {skill_name!r} entry in skill-effects.json must be a JSON object",
            )
        if "required_effects" not in skill_info:
            raise _PolicyError(
                "SCHEMA_ERROR",
                f"skill {skill_name!r} missing 'required_effects' in skill-effects.json",
            )
        if not isinstance(skill_info["required_effects"], list):
            raise _PolicyError(
                "SCHEMA_ERROR",
                f"skill {skill_name!r} 'required_effects' must be a JSON array, "
                f"got {type(skill_info['required_effects']).__name__!r}",
            )
        for eff in skill_info["required_effects"]:
            if not isinstance(eff, str):
                raise _PolicyError(
                    "SCHEMA_ERROR",
                    f"skill {skill_name!r} 'required_effects' contains non-string value: {eff!r}",
                )
            if eff not in _KNOWN_EFFECTS:
                raise _PolicyError(
                    "SCHEMA_ERROR",
                    f"skill {skill_name!r} has unknown effect {eff!r} in skill-effects.json",
                )

    # Step 3: Validate constrained role metadata completeness and read-only invariant.
    for role, is_optional in _CONSTRAINED_ROLES.items():
        if role not in roles_data:
            if not is_optional:
                raise _PolicyError(
                    "MISSING_ROLE_METADATA",
                    f"required constrained role {role!r} missing from skill-effects.json roles map",
                )
            continue
        role_entry = roles_data[role]
        if not isinstance(role_entry, dict):
            raise _PolicyError(
                "SCHEMA_ERROR",
                f"role {role!r} entry in skill-effects.json must be a JSON object",
            )
        if "allowed_effects" not in role_entry:
            raise _PolicyError(
                "SCHEMA_ERROR",
                f"role {role!r} missing 'allowed_effects' in skill-effects.json",
            )
        allowed_effects = role_entry["allowed_effects"]
        if not isinstance(allowed_effects, list):
            raise _PolicyError(
                "SCHEMA_ERROR",
                f"role {role!r} 'allowed_effects' must be a JSON array",
            )
        for effect in allowed_effects:
            if not isinstance(effect, str) or effect not in _KNOWN_EFFECTS:
                raise _PolicyError(
                    "SCHEMA_ERROR",
                    f"role {role!r} has invalid effect {effect!r} in 'allowed_effects'",
                )
        if "profile" in role_entry and (
            not isinstance(role_entry["profile"], str) or not role_entry["profile"]
        ):
            raise _PolicyError(
                "SCHEMA_ERROR",
                f"role {role!r} 'profile' must be a nonempty string",
            )
        if "optional" in role_entry and not isinstance(role_entry["optional"], bool):
            raise _PolicyError(
                "SCHEMA_ERROR",
                f"role {role!r} 'optional' must be a JSON boolean",
            )
        non_read = [e for e in allowed_effects if e != "read"]
        if non_read:
            raise _PolicyError(
                "SCHEMA_ERROR",
                f"constrained role {role!r} has non-read allowed_effects: {non_read!r}",
            )

    # Step 4: Read registry allowlist via safe AST analysis (no import/execution).
    allowlist = _read_allowlist(root)

    # Step 4b: Validate skill path safety, classification, and filesystem integrity
    # for EVERY referenced skill in ALL allowlisted roles (not only constrained ones).
    # Effects-subset and profile checks happen later (Step 5, constrained roles only).
    for role in sorted(allowlist):
        for skill in sorted(allowlist[role]):
            _validate_skill_path_and_fs(root, skill, role)
            if skill not in skills_data:
                raise _PolicyError(
                    "MISSING_CLASSIFICATION",
                    f"role {role!r}: skill {skill!r} has no entry in skill-effects.json",
                )

    # Step 5: Per-constrained-role validation (profile + effects subset checks).
    for role, is_optional in _CONSTRAINED_ROLES.items():
        # Determine profile path: use declared metadata if present, else default.
        if role in roles_data:
            profile_rel: str = roles_data[role].get("profile", f".github/agents/{role}.agent.md")
        else:
            profile_rel = f".github/agents/{role}.agent.md"

        # Validate profile path for safety before any filesystem access.
        profile_path = _check_profile_path_safe(root, profile_rel, role)

        has_registry = role in allowlist
        has_profile = profile_path.is_file() or profile_path.is_symlink()

        if is_optional:
            if not has_registry and not has_profile:
                print(
                    f"NOT_PRESENT: optional role {role!r} absent from registry "
                    f"and filesystem — skipping policy check"
                )
                continue
            if has_registry != has_profile:
                present_surface = "registry" if has_registry else "filesystem"
                raise _PolicyError(
                    "ONE_SIDED_OPTIONAL",
                    f"optional role {role!r} present on {present_surface!r} only; "
                    f"both surfaces or neither are required",
                )

        # Role is on both surfaces (or required). Metadata must exist.
        if role not in roles_data:
            raise _PolicyError(
                "MISSING_ROLE_METADATA",
                f"role {role!r} present on surfaces but absent from skill-effects.json roles map",
            )

        role_meta = roles_data[role]
        if not isinstance(role_meta, dict):
            raise _PolicyError(
                "SCHEMA_ERROR",
                f"role {role!r} entry in skill-effects.json must be a JSON object",
            )
        allowed_effects: list = role_meta["allowed_effects"]

        # Profile must not be a symlink.
        if profile_path.is_symlink():
            raise _PolicyError(
                "UNSAFE_PATH", f"role {role!r} profile is a symlink: {profile_rel!r}"
            )

        # Profile file must exist.
        if not profile_path.is_file():
            raise _PolicyError("MISSING_FILE", f"role {role!r} profile not found: {profile_rel!r}")

        # Profile must not list mutation tools.
        tools = _parse_profile_tools(profile_path)
        mutation_found = [t for t in tools if t in _MUTATION_TOOLS]
        if mutation_found:
            raise _PolicyError(
                "POLICY_VIOLATION",
                f"role {role!r} profile lists mutation tools: {mutation_found!r}",
            )

        # Check each skill referenced by this constrained role.
        role_skills = allowlist.get(role, set())
        for skill in sorted(role_skills):
            _check_skill(root, skill, role, allowed_effects, skills_data)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Agent-skill policy consistency checker (stdlib only)."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: inferred from script location).",
    )
    args = parser.parse_args()
    root = (args.root if args.root is not None else Path(__file__).resolve().parents[1]).resolve()

    try:
        _run_checks(root)
    except _PolicyError as exc:
        print(f"{exc.token}: {exc.msg}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"SCHEMA_ERROR: unexpected error during policy check: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
