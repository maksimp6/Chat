#!/usr/bin/env python3
"""Build a compact deterministic Python AST index for AI agents."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
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
        self.scope_kinds: list[str] = []
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
        self.scope_kinds.append("class")
        self.generic_visit(node)
        self.scope_kinds.pop()
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        kind = "method" if self.scope_kinds and self.scope_kinds[-1] == "class" else "function"
        self._add_symbol(node, node.name, kind)
        self.scope.append(node.name)
        self.scope_kinds.append("function")
        self.generic_visit(node)
        self.scope_kinds.pop()
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        kind = (
            "async_method"
            if self.scope_kinds and self.scope_kinds[-1] == "class"
            else "async_function"
        )
        self._add_symbol(node, node.name, kind)
        self.scope.append(node.name)
        self.scope_kinds.append("function")
        self.generic_visit(node)
        self.scope_kinds.pop()
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
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    return None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iter_python_files(root: Path) -> list[Path]:
    if (root / ".git").exists():
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--", "*.py"],
            check=True,
            capture_output=True,
        )
        relative_paths = [
            Path(item.decode("utf-8")) for item in completed.stdout.split(b"\0") if item
        ]
        return [
            root / relative
            for relative in sorted(relative_paths, key=lambda item: item.as_posix())
            if not any(part in EXCLUDED_PARTS for part in relative.parts)
        ]

    files: list[Path] = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
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


def _module_matches(imported: str, changed_modules: set[str]) -> bool:
    """True if `imported` exactly matches or is a sub-module of any changed module."""
    return imported in changed_modules or any(
        imported.startswith(m + ".") for m in changed_modules
    )


def query_affected(
    index: dict[str, Any],
    changed_paths: list[str],
    git_root: Path | None = None,
) -> dict[str, Any]:
    """Return modules and tests affected by the given list of changed file paths.

    Uses the pre-built index; no filesystem access beyond an optional git-revision
    lookup for provenance.  No mutation authority.
    """
    path_to_entry: dict[str, dict[str, Any]] = {f["path"]: f for f in index["files"]}

    # Per-file provenance and direct changed-module set
    changed_file_records: list[dict[str, Any]] = []
    changed_modules: set[str] = set()
    for p in changed_paths:
        entry = path_to_entry.get(p)
        changed_file_records.append(
            {
                "path": p,
                "in_index": entry is not None,
                "sha256_in_index": entry["sha256"] if entry else None,
            }
        )
        if entry:
            changed_modules.add(entry["module"])

    # First-order reverse deps: non-test modules that directly import a changed module
    affected_modules: set[str] = set(changed_modules)
    for f in index["files"]:
        if f["is_test"]:
            continue
        for imp in f["imports"]:
            imported = imp["module"].lstrip(".")
            if imported and _module_matches(imported, changed_modules):
                affected_modules.add(f["module"])
                break

    # Tests that cover any affected module via the pre-built tests_by_module map
    tests_by_module: dict[str, list[str]] = index.get("tests_by_module", {})
    affected_tests: set[str] = set()
    for module in affected_modules:
        affected_tests.update(tests_by_module.get(module, []))

    git_revision: str | None = None
    if git_root is not None:
        try:
            result = subprocess.run(
                ["git", "-C", str(git_root), "rev-parse", "HEAD"],
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                git_revision = result.stdout.decode().strip()
        except OSError:
            pass

    return {
        "schema_version": index["schema_version"],
        "query": "affected_modules",
        "provenance": {
            "git_revision": git_revision,
            "index_root": index["root"],
        },
        "changed_files": changed_file_records,
        "affected_modules": sorted(affected_modules),
        "affected_tests": sorted(affected_tests),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--output", help="write JSON to this path instead of stdout")
    parser.add_argument("--compact", action="store_true", help="emit compact JSON")
    parser.add_argument(
        "--query-affected",
        metavar="FILES",
        help="comma-separated changed file paths; requires --index-file",
    )
    parser.add_argument(
        "--index-file",
        metavar="PATH",
        help="pre-built index JSON consumed by --query-affected",
    )
    args = parser.parse_args()

    if args.query_affected is not None:
        if not args.index_file:
            parser.error("--query-affected requires --index-file")
        index = json.loads(Path(args.index_file).read_text(encoding="utf-8"))
        changed_paths = [p.strip() for p in args.query_affected.split(",") if p.strip()]
        result: dict[str, Any] = query_affected(index, changed_paths, git_root=Path(args.root))
    else:
        result = build_index(Path(args.root))

    text = json.dumps(
        result,
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
