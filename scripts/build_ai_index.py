#!/usr/bin/env python3
"""Build a compact deterministic Python AST index for AI agents."""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import quote
from typing import Any


SCHEMA_VERSION = 1
SNAPSHOT_SCHEMA_VERSION = 1
PARSER_VERSION = f"python-ast-v1:{sys.implementation.cache_tag}"
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
    return _index_source(relative, path.read_bytes())


def _index_source(relative: Path, data: bytes) -> dict[str, Any]:
    tree = ast.parse(data, filename=relative.as_posix())
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


def _assemble_index(files: list[dict[str, Any]]) -> dict[str, Any]:
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


def _git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    env = os.environ.copy()
    for key in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        env.pop(key, None)
    env.update(GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1")
    try:
        return subprocess.run(
            ["git", "--no-replace-objects", "-C", str(root), *args],
            input=stdin,
            check=True,
            capture_output=True,
            timeout=30,
            env=env,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        # Git errors can include paths/config; never echo raw stderr or source.
        raise ValueError("Git snapshot operation failed") from None


def _snapshot_tree(
    root: Path,
    revision: str,
    *,
    max_files: int,
    max_file_bytes: int,
    max_total_bytes: int,
) -> tuple[str, list[dict[str, Any]], dict[str, str]]:
    if not revision or revision.startswith("-") or any(ord(c) < 32 for c in revision):
        raise ValueError("invalid revision")
    try:
        sha = (
            _git(root, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}")
            .decode("ascii")
            .strip()
        )
    except ValueError:
        raise ValueError("revision is not an available Git commit") from None
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
        raise ValueError("invalid resolved revision")
    files = []
    skipped = {}
    total = 0
    for record in _git(root, "ls-tree", "--full-tree", "-r", "-l", "-z", sha).split(b"\0"):
        if not record:
            continue
        header, name = record.split(b"\t", 1)
        relative = Path(name.decode("utf-8"))
        if relative.suffix != ".py" or any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        path = relative.as_posix()
        if any(ord(c) < 32 or ord(c) == 127 for c in path):
            raise ValueError("unsupported control character in indexed path")
        mode, kind, oid, size = header.decode("ascii").split()
        if mode not in {"100644", "100755"} or kind != "blob":
            skipped[path] = "non_regular_file"
            continue
        length = int(size)
        total += length
        if length > max_file_bytes or total > max_total_bytes or len(files) >= max_files:
            raise ValueError("snapshot file/byte limit exceeded")
        files.append({"path": path, "oid": oid, "size": length})
    return sha, sorted(files, key=lambda f: f["path"]), skipped


def _snapshot_blobs(root: Path, files: list[dict[str, Any]]) -> dict[str, bytes]:
    unique = {item["oid"]: item["size"] for item in files}
    if not unique:
        return {}
    stream = io.BytesIO(
        _git(
            root, "cat-file", "--batch", stdin="".join(oid + "\n" for oid in unique).encode("ascii")
        )
    )
    blobs = {}
    for oid, size in unique.items():
        header = stream.readline().decode("ascii").split()
        if header != [oid, "blob", str(size)]:
            raise ValueError("unexpected Git blob response")
        data = stream.read(size)
        if len(data) != size or stream.read(1) != b"\n":
            raise ValueError("incomplete Git blob response")
        blobs[oid] = data
    if stream.read():
        raise ValueError("unexpected trailing Git blob response")
    return blobs


def _validate_cached_ast(item: dict[str, Any]) -> None:
    for symbol in item["symbols"]:
        if (
            not isinstance(symbol, dict)
            or any(
                not isinstance(symbol.get(key), str) for key in ("name", "qualified_name", "kind")
            )
            or any(
                type(symbol.get(key)) is not int or symbol[key] < 1 for key in ("line", "end_line")
            )
        ):
            raise ValueError("incompatible previous index symbol")
    if any(
        not isinstance(imp, dict)
        or not isinstance(imp.get("module"), str)
        or not {"name", "as"} <= imp.keys()
        for imp in item["imports"]
    ):
        raise ValueError("incompatible previous index import")
    if any(not isinstance(call, str) for call in item["calls"]):
        raise ValueError("incompatible previous index call")


def _validate_cached_entry(item: dict[str, Any]) -> None:
    if (
        not isinstance(item.get("module"), str)
        or item["module"] != _module_name(Path(item["path"]))
        or type(item.get("is_test")) is not bool
        or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", str(item.get("git_blob_oid", "")))
    ):
        raise ValueError("incompatible previous index identity")
    for key in ("symbols", "imports", "calls"):
        if not isinstance(item.get(key), list):
            raise ValueError("incompatible previous index entry")
    if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))):
        raise ValueError("incompatible previous index content hash")
    _validate_cached_ast(item)


def _previous_files(previous: dict[str, Any] | None, repository: str) -> dict[str, dict[str, Any]]:
    if previous is None:
        return {}
    expected = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "parser_version": PARSER_VERSION,
        "repository": repository,
    }
    if not isinstance(previous, dict) or any(
        previous.get(key) != value for key, value in expected.items()
    ):
        raise ValueError("incompatible previous index; rebuild without the cache")
    files = previous.get("files")
    if not isinstance(files, list):
        raise ValueError("incompatible previous index files")
    by_path = {}
    for item in files:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or item["path"] in by_path
        ):
            raise ValueError("incompatible previous index entry")
        _validate_cached_entry(item)
        by_path[item["path"]] = item
    return by_path


def _require_complete_repository(root: Path) -> None:
    config_names = (
        _git(root, "config", "--list", "--name-only").decode("utf-8").lower().splitlines()
    )
    if any(
        name == "extensions.partialclone"
        or (name.startswith("remote.") and name.endswith(".promisor"))
        for name in config_names
    ):
        raise ValueError("partial clone is not supported; provide a complete local repository")


def build_index(
    root: Path,
    *,
    revision: str | None = None,
    repository: str | None = None,
    previous_index: dict[str, Any] | None = None,
    stats: dict[str, int] | None = None,
    max_files: int = 5000,
    max_file_bytes: int = 1024 * 1024,
    max_total_bytes: int = 32 * 1024 * 1024,
) -> dict[str, Any]:
    """Build the legacy worktree map or an opt-in immutable Git snapshot.

    Previous indexes are trusted local derived artifacts, not authenticated input.
    Reuse avoids AST parsing, not Git tree enumeration. No models or network API.
    """
    root = root.resolve()
    if revision is None:
        if repository is not None or previous_index is not None or stats is not None:
            raise ValueError("snapshot options require a revision")
        return _assemble_index([_index_file(root, path) for path in _iter_python_files(root)])
    if not isinstance(repository, str) or not re.fullmatch(
        r"[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*", repository
    ):
        raise ValueError("repository must be an explicit owner/name")
    repository = repository.lower()
    if any(
        type(limit) is not int or limit < 1
        for limit in (max_files, max_file_bytes, max_total_bytes)
    ):
        raise ValueError("snapshot limits must be positive integers")
    previous = _previous_files(previous_index, repository)
    _require_complete_repository(root)
    sha, tree, skipped = _snapshot_tree(
        root,
        revision,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )
    reusable = {
        item["path"]
        for item in tree
        if previous.get(item["path"], {}).get("git_blob_oid") == item["oid"]
    }
    blobs = _snapshot_blobs(root, [item for item in tree if item["path"] not in reusable])
    files = []
    for item in tree:
        path = item["path"]
        if path in reusable:
            entry = copy.deepcopy(previous[path])
        else:
            try:
                entry = _index_source(Path(path), blobs[item["oid"]])
            except (SyntaxError, UnicodeError, ValueError):
                raise ValueError(f"cannot parse Python source: {path}") from None
        entry["git_blob_oid"] = item["oid"]
        entry["source_id"] = f"{repository}:code:{quote(path, safe='/')}"
        entry["source_ref"] = f"https://github.com/{repository}/blob/{sha}/{quote(path, safe='/')}"
        for symbol in entry["symbols"]:
            symbol["source_ref"] = f"{entry['source_ref']}#L{symbol['line']}-L{symbol['end_line']}"
        files.append(entry)
    result = {
        **_assemble_index(files),
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "parser_version": PARSER_VERSION,
        "repository": repository,
        "source_version": sha,
        "source_kind": "git_commit",
        "skipped_paths": dict(sorted(skipped.items())),
    }
    if stats is not None:
        stats.update(
            parsed_files=len(tree) - len(reusable),
            reused_files=len(reusable),
            deleted_files=len(set(previous) - {item["path"] for item in tree}),
        )
    return result


def _write_json(path: Path, text: str) -> None:
    """Replace a derived artifact only after a successful complete build."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(text + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _reverse_dependencies(index: dict[str, Any], direct_modules: set[str]) -> set[str]:
    reverse_deps: set[str] = set()
    for file_entry in index.get("files", []):
        if file_entry["module"] in direct_modules:
            continue
        imported = {
            str(imp.get("module") or "").lstrip(".") for imp in file_entry.get("imports", [])
        }
        if any(
            imp and (imp == module or imp.startswith(module + "."))
            for imp in imported
            for module in direct_modules
        ):
            reverse_deps.add(file_entry["module"])
    return reverse_deps


def _affected_tests(tests_by_module: dict[str, list[str]], modules: list[str]) -> list[str]:
    affected: set[str] = set()
    for module in modules:
        affected.update(tests_by_module.get(module, []))
        affected.update(tests_by_module.get(module.split(".", 1)[0], []))
    return sorted(affected)


def query_affected(
    index: dict[str, Any],
    changed_paths: list[str],
    *,
    git_revision: str | None = None,
) -> dict[str, Any]:
    """Return affected Python modules and tests for a list of changed file paths.

    First-order reverse dependencies only. No filesystem access; all data is
    derived from the pre-built *index*.
    """
    bound_revision = index.get("source_version") if index.get("snapshot_schema_version") else None
    if bound_revision is not None and git_revision is not None and git_revision != bound_revision:
        raise ValueError("revision does not match the immutable index")
    by_path: dict[str, dict[str, Any]] = {f["path"]: f for f in index.get("files", [])}
    tests_by_module: dict[str, list[str]] = index.get("tests_by_module", {})

    # Resolve each changed path to its indexed module, recording provenance.
    direct_modules: set[str] = set()
    provenance_files: dict[str, dict[str, Any]] = {}
    for path in changed_paths:
        entry = by_path.get(path)
        prov: dict[str, Any] = {"in_index": entry is not None}
        if entry is not None:
            prov["module"] = entry["module"]
            prov["sha256_in_index"] = entry["sha256"]
            direct_modules.add(entry["module"])
            if bound_revision is not None:
                prov["source_ref"] = entry["source_ref"]
        provenance_files[path] = prov

    affected_modules = sorted(direct_modules | _reverse_dependencies(index, direct_modules))
    affected_tests = _affected_tests(tests_by_module, affected_modules)

    provenance: dict[str, Any] = {
        "index_schema_version": index.get("schema_version"),
        "files": provenance_files,
    }
    if bound_revision is not None:
        provenance["git_revision"] = bound_revision
        provenance["repository"] = index["repository"]
    elif git_revision is not None:
        provenance["git_revision"] = git_revision

    return {
        "schema_version": index.get("schema_version"),
        "query": "affected_modules",
        "changed_files": sorted(changed_paths),
        "affected_modules": affected_modules,
        "affected_tests": affected_tests,
        "provenance": provenance,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--output", help="write JSON to this path instead of stdout")
    parser.add_argument("--compact", action="store_true", help="emit compact JSON")
    parser.add_argument(
        "--query-affected",
        metavar="FILE",
        nargs="+",
        dest="query_affected",
        help="query affected modules/tests for these changed file paths (requires --index-file)",
    )
    parser.add_argument(
        "--index-file",
        metavar="PATH",
        help="pre-built index JSON for --query-affected",
    )
    parser.add_argument(
        "--git-revision",
        metavar="REV",
        help="git revision to embed in --query-affected provenance",
    )
    parser.add_argument(
        "--revision", help="index this immutable Git commit, ignoring working-tree edits"
    )
    parser.add_argument(
        "--repository", help="explicit GitHub owner/name for snapshot namespace/source refs"
    )
    parser.add_argument(
        "--previous-index", help="trusted prior snapshot JSON; requires --revision/--repository"
    )
    parser.add_argument("--max-files", type=int, default=5000, help="snapshot file limit")
    parser.add_argument(
        "--max-file-bytes", type=int, default=1024 * 1024, help="snapshot per-file byte limit"
    )
    parser.add_argument(
        "--max-total-bytes", type=int, default=32 * 1024 * 1024, help="snapshot total byte limit"
    )
    args = parser.parse_args()
    if args.query_affected is not None and (
        args.revision is not None or args.repository is not None or args.previous_index is not None
    ):
        parser.error("snapshot build options cannot be combined with --query-affected")

    if args.query_affected is not None:
        if not args.index_file:
            parser.error("--query-affected requires --index-file")
        index = json.loads(Path(args.index_file).read_text(encoding="utf-8"))
        result = query_affected(
            index,
            args.query_affected,
            git_revision=args.git_revision,
        )
        text = json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if args.compact else 2,
            separators=(",", ":") if args.compact else None,
        )
        if args.output:
            output = Path(args.output)
            _write_json(output, text)
        else:
            print(text)
        return 0

    previous = (
        json.loads(Path(args.previous_index).read_text(encoding="utf-8"))
        if args.previous_index
        else None
    )
    stats = {} if args.revision is not None else None
    index = build_index(
        Path(args.root),
        revision=args.revision,
        repository=args.repository,
        previous_index=previous,
        stats=stats,
        max_files=args.max_files,
        max_file_bytes=args.max_file_bytes,
        max_total_bytes=args.max_total_bytes,
    )
    if stats is not None:
        print(json.dumps(stats, sort_keys=True), file=sys.stderr)
    text = json.dumps(
        index,
        ensure_ascii=False,
        sort_keys=True,
        indent=None if args.compact else 2,
        separators=(",", ":") if args.compact else None,
    )
    if args.output:
        output = Path(args.output)
        _write_json(output, text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError):
        # Fail without including credentials, source lines or arbitrary exception text.
        print(
            "Repository index failed: invalid input, unavailable revision, incompatible cache, limit or I/O error.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
