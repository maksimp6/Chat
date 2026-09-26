#!/usr/bin/env python3
"""Static policy validator for Alice Pro preview/runtime Python modules."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "runtime"
BOUNDARY_FILES = {RUNTIME_DIR / "dispatcher.py"}

FORBIDDEN_IMPORT_ROOTS = {
    "db",
    "mcp_storage",
    "requests",
    "httpx",
    "socket",
    "sqlite3",
    "subprocess",
    "tool_registry",
    "urllib.request",
}

FORBIDDEN_CALL_NAMES = {
    "get_conn",
    "open",
}

FORBIDDEN_ATTRIBUTE_CALLS = {
    ("Path", "open"),
    ("Path", "read_bytes"),
    ("Path", "read_text"),
    ("Path", "rename"),
    ("Path", "replace"),
    ("Path", "unlink"),
    ("Path", "write_bytes"),
    ("Path", "write_text"),
    ("os", "remove"),
    ("os", "rename"),
    ("os", "replace"),
    ("os", "unlink"),
    ("requests", "delete"),
    ("requests", "get"),
    ("requests", "patch"),
    ("requests", "post"),
    ("requests", "put"),
    ("requests", "request"),
    ("subprocess", "Popen"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("subprocess", "run"),
}


def _root_name(name: str) -> str:
    parts = name.split(".")
    if len(parts) >= 2 and ".".join(parts[:2]) == "urllib.request":
        return "urllib.request"
    return parts[0]


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    values: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        values.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        values.append(current.id)
    return tuple(reversed(values))


def validate_source(source: str, label: str = "<runtime>") -> list[str]:
    try:
        tree = ast.parse(source, filename=label)
    except SyntaxError as exc:
        return [f"{label}:{exc.lineno or 0}: Python syntax error: {exc.msg}"]

    errors: list[str] = []
    aliases: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                root = _root_name(item.name)
                aliases[item.asname or item.name.split(".")[0]] = root
                if root in FORBIDDEN_IMPORT_ROOTS:
                    errors.append(
                        f"{label}:{node.lineno}: runtime dispatcher violation: "
                        f"direct import of {root} is forbidden"
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = _root_name(module)
            for item in node.names:
                aliases[item.asname or item.name] = root
            if root in FORBIDDEN_IMPORT_ROOTS:
                errors.append(
                    f"{label}:{node.lineno}: runtime dispatcher violation: "
                    f"direct import from {root} is forbidden"
                )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        if isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALL_NAMES:
                errors.append(
                    f"{label}:{node.lineno}: runtime dispatcher violation: "
                    f"direct {node.func.id}() access is forbidden"
                )
            imported_from = aliases.get(node.func.id)
            if imported_from in FORBIDDEN_IMPORT_ROOTS:
                errors.append(
                    f"{label}:{node.lineno}: runtime dispatcher violation: "
                    f"direct call through {imported_from} is forbidden"
                )
            continue

        chain = _attribute_chain(node.func)
        if len(chain) < 2:
            continue
        owner, method = chain[-2], chain[-1]
        owner = aliases.get(owner, owner)
        if (owner, method) in FORBIDDEN_ATTRIBUTE_CALLS:
            errors.append(
                f"{label}:{node.lineno}: runtime dispatcher violation: "
                f"direct {owner}.{method}() access is forbidden"
            )

    return errors


def validate_file(path: Path) -> list[str]:
    if path in BOUNDARY_FILES or path.name == "__init__.py":
        return []
    return validate_source(path.read_text(encoding="utf-8"), str(path.relative_to(ROOT)))


def main() -> int:
    files = sorted(RUNTIME_DIR.rglob("*.py"))
    errors: list[str] = []
    for path in files:
        errors.extend(validate_file(path))

    if errors:
        print("Runtime policy validation failed:")
        for error in errors:
            print(f"ERROR {error}")
        print(f"\n{len(errors)} error(s). Runtime modules must use RuntimeDispatcher.")
        return 1

    print(
        f"Runtime policy validation passed: {len(files)} Python file(s), 0 dispatcher violations."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
