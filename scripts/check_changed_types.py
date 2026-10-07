#!/usr/bin/env python3
"""Forward-only function documentation and typing policy for production Python."""

from __future__ import annotations

import argparse
import ast
import subprocess
from collections import Counter
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
_BARE_GENERICS = {"Callable", "dict", "frozenset", "list", "set", "tuple", "type"}
_EXCLUDED_PREFIXES = ("android/", "deploy/", "plugins/", "r/", "scripts/", "tests/")


class Violation(NamedTuple):
    """One deterministic changed-function contract violation."""

    path: str
    line: int
    function: str
    rule: str
    message: str


class FunctionRecord(NamedTuple):
    """Parsed function plus stable identity and comparison metadata."""

    key: str
    qualname: str
    line: int
    end_line: int
    nested: bool
    node: ast.FunctionDef | ast.AsyncFunctionDef
    fingerprint: str


class _FunctionCollector(ast.NodeVisitor):
    """Collect functions with stable qualified names and occurrence indexes."""

    def __init__(self) -> None:
        self.records: dict[str, FunctionRecord] = {}
        self._scope: list[str] = []
        self._function_depth = 0
        self._occurrences: Counter[str] = Counter()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = ".".join([*self._scope, node.name])
        occurrence = self._occurrences[qualname]
        self._occurrences[qualname] += 1
        key = f"{qualname}#{occurrence}"
        end_line = node.end_lineno or node.lineno
        self.records[key] = FunctionRecord(
            key=key,
            qualname=qualname,
            line=node.lineno,
            end_line=end_line,
            nested=self._function_depth > 0,
            node=node,
            fingerprint=ast.dump(node, include_attributes=False),
        )
        self._scope.append(node.name)
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1
        self._scope.pop()


def _parse_functions(path: str, source: str) -> dict[str, FunctionRecord]:
    tree = ast.parse(source, filename=path)
    collector = _FunctionCollector()
    collector.visit(tree)
    return collector.records


def _annotation_name(annotation: ast.expr | None) -> str | None:
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    return None


def _bare_generic_names(annotation: ast.expr | None) -> set[str]:
    if annotation is None:
        return set()
    if isinstance(annotation, ast.Subscript):
        return _bare_generic_names(annotation.slice)
    name = _annotation_name(annotation)
    if name in _BARE_GENERICS:
        return {name}
    names: set[str] = set()
    for child in ast.iter_child_nodes(annotation):
        if isinstance(child, ast.expr):
            names.update(_bare_generic_names(child))
    return names


def _contains_any(annotation: ast.expr | None) -> bool:
    if annotation is None:
        return False
    return any(
        _annotation_name(node) == "Any"
        for node in ast.walk(annotation)
        if isinstance(node, ast.expr)
    )


def _is_stub(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if len(node.body) != 1:
        return False
    statement = node.body[0]
    if isinstance(statement, ast.Pass):
        return True
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and statement.value.value is Ellipsis
    )


def _arguments(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg]:
    items = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    if node.args.vararg is not None:
        items.append(node.args.vararg)
    if node.args.kwarg is not None:
        items.append(node.args.kwarg)
    return items


def _parameter_violations(path: str, record: FunctionRecord) -> list[Violation]:
    violations: list[Violation] = []
    for argument in _arguments(record.node):
        if argument.arg in {"self", "cls"}:
            continue
        if argument.annotation is None:
            violations.append(
                Violation(
                    path,
                    record.line,
                    record.qualname,
                    "missing-parameter",
                    f"parameter {argument.arg!r} has no annotation",
                )
            )
    return violations


def _annotation_violations(path: str, record: FunctionRecord) -> list[Violation]:
    node = record.node
    violations: list[Violation] = []
    if node.returns is None:
        violations.append(
            Violation(
                path,
                record.line,
                record.qualname,
                "missing-return",
                "function has no return annotation",
            )
        )
    annotations = [argument.annotation for argument in _arguments(node)]
    annotations.append(node.returns)
    for annotation in annotations:
        for generic in sorted(_bare_generic_names(annotation)):
            violations.append(
                Violation(
                    path,
                    record.line,
                    record.qualname,
                    "unparameterized-generic",
                    f"bare generic {generic!r} must be parameterized",
                )
            )
    if _contains_any(node.returns):
        violations.append(
            Violation(
                path,
                record.line,
                record.qualname,
                "any-leak",
                "Any must not leak through a production return type",
            )
        )
    return violations


def _docstring_violations(path: str, record: FunctionRecord) -> list[Violation]:
    node = record.node
    is_public = not node.name.startswith("_") and not record.nested
    if not is_public or _is_stub(node) or ast.get_docstring(node, clean=False):
        return []
    return [
        Violation(
            path,
            record.line,
            record.qualname,
            "missing-docstring",
            "public production function or method has no docstring",
        )
    ]


def _ignore_violations(
    path: str,
    record: FunctionRecord,
    source_lines: list[str],
) -> list[Violation]:
    violations: list[Violation] = []
    for lineno in range(record.line, record.end_line + 1):
        line = source_lines[lineno - 1]
        marker = line.partition("# type: ignore")
        if marker[1] and "[" not in marker[2]:
            violations.append(
                Violation(
                    path,
                    lineno,
                    record.qualname,
                    "type-ignore-policy",
                    "type: ignore must name a narrow mypy error code",
                )
            )
    return violations


def _record_violations(
    path: str,
    record: FunctionRecord,
    source_lines: list[str],
) -> list[Violation]:
    return [
        *_parameter_violations(path, record),
        *_annotation_violations(path, record),
        *_docstring_violations(path, record),
        *_ignore_violations(path, record, source_lines),
    ]


def check_source(path: str, source: str, *, is_new: bool) -> list[Violation]:
    """Check every function in a supplied new or changed source fixture."""
    del is_new
    records = _parse_functions(path, source)
    lines = source.splitlines()
    violations: list[Violation] = []
    for record in records.values():
        violations.extend(_record_violations(path, record, lines))
    return violations


def check_changed_source(path: str, before: str | None, after: str) -> list[Violation]:
    """Check only functions whose AST changed relative to the base source."""
    after_records = _parse_functions(path, after)
    before_records = {} if before is None else _parse_functions(path, before)
    lines = after.splitlines()
    violations: list[Violation] = []
    for key, record in after_records.items():
        previous = before_records.get(key)
        if previous is not None and previous.fingerprint == record.fingerprint:
            continue
        violations.extend(_record_violations(path, record, lines))
    return violations


def _is_production_path(path: str) -> bool:
    return path.endswith(".py") and not path.startswith(_EXCLUDED_PREFIXES)


def _git(
    root: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=check,
    )


def _changed_python_paths(
    root: Path,
    base: str,
    head: str,
) -> list[tuple[str | None, str]]:
    result = _git(
        root,
        "diff",
        "--find-renames",
        "--name-status",
        base,
        head,
        "--",
        "*.py",
    )
    changed: list[tuple[str | None, str]] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        status = parts[0]
        if status.startswith(("R", "C")) and len(parts) == 3:
            before_path, after_path = parts[1], parts[2]
        elif status == "A" and len(parts) == 2:
            before_path, after_path = None, parts[1]
        elif status == "M" and len(parts) == 2:
            before_path = after_path = parts[1]
        else:
            continue
        if _is_production_path(after_path):
            changed.append((before_path, after_path))
    return changed


def _git_source(root: Path, ref: str, path: str) -> str:
    result = _git(root, "show", f"{ref}:{path}", check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"cannot read {path!r} from {ref!r}: {result.stderr.strip()}"
        )
    return result.stdout


def check_repository(
    base: str,
    head: str = "HEAD",
    *,
    root: Path = ROOT,
) -> list[Violation]:
    """Check changed production functions between two repository revisions."""
    _git(root, "rev-parse", "--verify", f"{base}^{{commit}}")
    _git(root, "rev-parse", "--verify", f"{head}^{{commit}}")
    violations: list[Violation] = []
    for before_path, after_path in _changed_python_paths(root, base, head):
        before = None if before_path is None else _git_source(root, base, before_path)
        after = _git_source(root, head, after_path)
        violations.extend(check_changed_source(after_path, before, after))
    return violations


def _format_violation(violation: Violation) -> str:
    return (
        f"{violation.path}:{violation.line}: {violation.function}: "
        f"{violation.rule}: {violation.message}"
    )


def main(argv: list[str] | None = None) -> int:
    """Run the changed-function contract checker."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="HEAD^")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)
    try:
        violations = check_repository(args.base, args.head)
    except (OSError, RuntimeError, subprocess.CalledProcessError, SyntaxError) as exc:
        print(f"changed-function check failed closed: {exc}")
        return 2
    for violation in violations:
        print(_format_violation(violation))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
