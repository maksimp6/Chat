#!/usr/bin/env python3
"""Inventory statically named Python environment reads and validate their docs reference."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import sys
from typing import Any

ALLOWED_CLASSES = {"public", "secret", "secret-reference", "ci-only", "compatibility"}
SKIP_PARTS = {"tests", ".venv", "node_modules", "android"}


def _literal_string(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


class EnvVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.keys: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in {"getenv", "get"} and node.args:
            owner = func.value
            is_getenv = isinstance(owner, ast.Name) and owner.id == "os" and func.attr == "getenv"
            is_environ_get = (
                isinstance(owner, ast.Attribute)
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "os"
                and owner.attr == "environ"
                and func.attr == "get"
            )
            if is_getenv or is_environ_get:
                key = _literal_string(node.args[0])
                if key:
                    self.keys.add(key)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        owner = node.value
        if (
            isinstance(owner, ast.Attribute)
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "os"
            and owner.attr == "environ"
        ):
            key = _literal_string(node.slice)
            if key:
                self.keys.add(key)
        self.generic_visit(node)


def discover_python_env(files: list[Path]) -> set[str]:
    keys: set[str] = set()
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = EnvVisitor()
        visitor.visit(tree)
        keys.update(visitor.keys)
    return keys


def discover_runtime_python(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if not any(part in SKIP_PARTS for part in path.relative_to(root).parts)
    )


def validate_reference(discovered: set[str], reference: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in sorted(discovered - set(reference)):
        errors.append(f"{key}: missing from environment variable reference")
    for key, entry in sorted(reference.items()):
        if not isinstance(entry, dict):
            errors.append(f"{key}: reference entry must be an object")
            continue
        if "value" in entry:
            errors.append(f"{key}: reference entry must not contain a value field")
        if entry.get("class") not in ALLOWED_CLASSES:
            errors.append(f"{key}: invalid class: {entry.get('class')}")
        if not isinstance(entry.get("purpose"), str) or not entry["purpose"].strip():
            errors.append(f"{key}: purpose is required")
    return errors


def render_markdown(reference: dict[str, Any]) -> str:
    lines = [
        "# Environment variables",
        "",
        "Generated from `environment-variables.json`. Values and secret material are intentionally excluded.",
        "",
        "Coverage: statically named environment reads in repository Python outside tests and `android/`. Dynamic names, shell/JavaScript/Kotlin and Android Python are follow-up surfaces, not silently assumed covered.",
        "",
        "| Variable | Class | Purpose |",
        "| --- | --- | --- |",
    ]
    for key, entry in sorted(reference.items()):
        lines.append(f"| `{key}` | {entry['class']} | {entry['purpose']} |")
    lines.extend(
        [
            "",
            "Classes:",
            "",
            "- `public` — non-secret runtime configuration;",
            "- `secret` — credential/bootstrap secret; document purpose, never value;",
            "- `secret-reference` — identifier/version/reference to a secret, not plaintext;",
            "- `ci-only` — test/CI control not intended as application configuration;",
            "- `compatibility` — legacy or transitional runtime input.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    reference_path = root / "docs" / "configuration" / "environment-variables.json"
    reference = (
        json.loads(reference_path.read_text(encoding="utf-8")) if reference_path.exists() else {}
    )
    discovered = discover_python_env(discover_runtime_python(root))
    errors = validate_reference(discovered, reference)
    if errors:
        for error in errors:
            print(f"env-ref: {error}", file=sys.stderr)
        return 1
    rendered = render_markdown(reference)
    markdown_path = reference_path.with_suffix(".md")
    if markdown_path.exists() and markdown_path.read_text(encoding="utf-8") != rendered:
        print("env-ref: generated environment-variables.md is stale", file=sys.stderr)
        return 1
    print(f"Environment reference valid: {len(discovered)} static Python keys")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
