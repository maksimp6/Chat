#!/usr/bin/env python3
"""Changed-code typing policy for Alice Pro.

The repository-wide semantic authority remains mypy --strict. This lightweight
checker gives immediate diagnostics for annotation debt introduced in new code.
"""

from __future__ import annotations

import ast
from typing import NamedTuple


class Violation(NamedTuple):
    path: str
    line: int
    function: str
    rule: str
    message: str


_BARE_GENERICS = {"dict", "list", "tuple", "set", "frozenset", "type", "Callable"}


def _name(annotation: ast.expr | None) -> str | None:
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    return None


def _arguments(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg]:
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    if node.args.vararg is not None:
        arguments.append(node.args.vararg)
    if node.args.kwarg is not None:
        arguments.append(node.args.kwarg)
    return arguments


def _check_function(path: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[Violation]:
    violations: list[Violation] = []
    arguments = _arguments(node)

    for argument in arguments:
        if argument.arg not in {"self", "cls"} and argument.annotation is None:
            violations.append(
                Violation(
                    path,
                    node.lineno,
                    node.name,
                    "missing-parameter",
                    f"parameter {argument.arg!r} has no annotation",
                )
            )

    if node.returns is None:
        violations.append(
            Violation(
                path,
                node.lineno,
                node.name,
                "missing-return",
                "function has no return annotation",
            )
        )

    annotations = [argument.annotation for argument in arguments]
    annotations.append(node.returns)
    for annotation in annotations:
        name = _name(annotation)
        if name in _BARE_GENERICS:
            violations.append(
                Violation(
                    path,
                    node.lineno,
                    node.name,
                    "unparameterized-generic",
                    f"bare generic {name!r} must be parameterized",
                )
            )

    if _name(node.returns) == "Any":
        violations.append(
            Violation(
                path,
                node.lineno,
                node.name,
                "any-leak",
                "Any must not leak through a production return type",
            )
        )
    return violations


def _check_ignores(path: str, source: str) -> list[Violation]:
    violations: list[Violation] = []
    for lineno, line in enumerate(source.splitlines(), 1):
        suffix = line.partition("# type: ignore")[2]
        if "# type: ignore" in line and "[" not in suffix:
            violations.append(
                Violation(
                    path,
                    lineno,
                    "<module>",
                    "type-ignore-policy",
                    "type: ignore must name a narrow mypy error code",
                )
            )
    return violations


def check_source(path: str, source: str, *, is_new: bool) -> list[Violation]:
    """Return deterministic typing-policy violations for one Python source file."""
    del is_new
    tree = ast.parse(source, filename=path)
    violations = _check_ignores(path, source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            violations.extend(_check_function(path, node))
    return violations
