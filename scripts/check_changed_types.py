#!/usr/bin/env python3
"""Changed-code typing policy for Alice Pro.

The repository-wide semantic authority remains mypy --strict. This lightweight
checker gives immediate diagnostics for annotation debt introduced in new code.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True)
class Violation:
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


def check_source(path: str, source: str, *, is_new: bool) -> list[Violation]:
    """Return deterministic typing-policy violations for one Python source file."""
    del is_new  # All supplied changed/new source is held to the same forward-only contract.
    tree = ast.parse(source, filename=path)
    violations: list[Violation] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if node.args.vararg is not None:
            arguments.append(node.args.vararg)
        if node.args.kwarg is not None:
            arguments.append(node.args.kwarg)

        for argument in arguments:
            if argument.arg in {"self", "cls"}:
                continue
            if argument.annotation is None:
                violations.append(
                    Violation(path, node.lineno, node.name, "missing-parameter",
                              f"parameter {argument.arg!r} has no annotation")
                )

        if node.returns is None:
            violations.append(
                Violation(path, node.lineno, node.name, "missing-return",
                          "function has no return annotation")
            )

        annotations = [argument.annotation for argument in arguments]
        annotations.append(node.returns)
        for annotation in annotations:
            name = _name(annotation)
            if name in _BARE_GENERICS:
                violations.append(
                    Violation(path, node.lineno, node.name, "unparameterized-generic",
                              f"bare generic {name!r} must be parameterized")
                )

        if _name(node.returns) == "Any":
            violations.append(
                Violation(path, node.lineno, node.name, "any-leak",
                          "Any must not leak through a production return type")
            )

    for lineno, line in enumerate(source.splitlines(), 1):
        if "# type: ignore" in line and "[" not in line.partition("# type: ignore")[2]:
            violations.append(
                Violation(path, lineno, "<module>", "type-ignore-policy",
                          "type: ignore must name a narrow mypy error code")
            )

    return violations
