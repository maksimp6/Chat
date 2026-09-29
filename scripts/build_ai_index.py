#!/usr/bin/env python3
"""Build a compact deterministic Python AST index for AI agents."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "coverage-js",
}


def _module_name(relative: Path) -> str:
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


class _IndexVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope: list[str] = []
        self.symbols: list[dict[str, Any]] = []
        self.imports: list[dict[str, Any]] = []
        self.calls: set[str] = set()

    def _qualified(self, name: str) -> str:
        return ".".join([*self.scope, name]) if self.scope else name

    def _add_symbol(self, node: ast.AST, name: str, kind: str) -> None:
        self.symbols.append(
            {
                "name": name,
                "qualified_name": self._qualified(name),
                "kind": kind,
                "line": int(getattr(node, "lineno", 0) or 0),
                "end_line": int(getattr(node, "end_lineno", 0) or 0),
            }
        )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._add_symbol(node, node.name, "class")
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        kind = "method" if self.scope else "function"
        self._add_symbol(node, node.name, kind)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        kind = "async_method" if self.scope else "async_function"
        self._add_symbol(node, node.name, kind)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            self.imports.append(
                {
                    "module": item.name,
                    "name": None,
                    "as": item.asname,
                }
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = "." * int(node.level or 0) + (node.module or "")
        for item in node.names:
            self.imports.append(
                {
                    "module": module,
                    "name": item.name,
                    "as": item.asname,
                }
            )

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        if name:
            self.calls.add(name)
        self.generic_visit(node)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iter_python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.py"):
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        if path.is_file():
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _index_file(root: Path, path: Path) -> dict[str, Any]:
    relative = path.relative_to(root)
    data = path.read_bytes()
    source = data.decode("utf-8")
    tree = ast.parse(source, filename=relative.as_posix())
    visitor = _IndexVisitor()
    visitor.visit(tree)
    module = _module_name(relative)

    return {
        "path": relative.as_posix(),
        "module": module,
        "sha256": _sha256(data),
        "is_test": relative.parts[0] == "tests" or relative.name.startswith("test_"),
        "symbols": sorted(
            visitor.symbols,
            key=lambda item: (item["line"], item["qualified_name"], item["kind"]),
        ),
        "imports": sorted(
            visitor.imports,
            key=lambda item: (
                item["module"],
                item["name"] or "",
                item["as"] or "",
            ),
        ),
        "calls": sorted(visitor.calls),
    }


def _tests_by_module(files: list[dict[str, Any]]) -> dict[str, list[str]]:
    mapping: dict[str, set[str]] = {}

    for item in files:
        if not item["is_test"]:
            continue
        test_path = item["path"]
        for imported in item["imports"]:
            module = str(imported["module"]).lstrip(".")
            if not module:
                continue
            mapping.setdefault(module, set()).add(test_path)
            first = module.split(".", 1)[0]
            mapping.setdefault(first, set()).add(test_path)

    return {module: sorted(paths) for module, paths in sorted(mapping.items())}


def build_index(root: Path) -> dict[str, Any]:
    root = root.resolve()
    files = [_index_file(root, path) for path in _iter_python_files(root)]
    symbol_count = sum(len(item["symbols"]) for item in files)

    return {
        "schema_version": SCHEMA_VERSION,
        "language": "python",
        "root": ".",
        "summary": {
            "files": len(files),
            "symbols": symbol_count,
            "test_files": sum(1 for item in files if item["is_test"]),
        },
        "files": files,
        "tests_by_module": _tests_by_module(files),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--output", help="write JSON to this path instead of stdout")
    parser.add_argument("--compact", action="store_true", help="emit compact JSON")
    args = parser.parse_args()

    index = build_index(Path(args.root))
    text = json.dumps(
        index,
        ensure_ascii=False,
        sort_keys=True,
        indent=None if args.compact else 2,
        separators=(",", ":") if args.compact else None,
    )
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
