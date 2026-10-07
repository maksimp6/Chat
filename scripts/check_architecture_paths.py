#!/usr/bin/env python3
"""Fail fast when added or renamed files violate proven repository boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import sys


@dataclass(frozen=True)
class Change:
    status: str
    path: str
    old_path: str | None = None


def _violation(change: Change) -> str | None:
    path = PurePosixPath(change.path)
    if change.status not in {"A", "R"}:
        return f"unsupported change status {change.status!r}"
    if path.is_absolute() or ".." in path.parts:
        return "path escapes repository root"
    if path.parts and path.parts[0] == "docs" and path.suffix == ".py":
        return "Python implementation files do not belong under docs/"
    return None


def validate_changes(changes: list[Change]) -> None:
    for change in changes:
        reason = _violation(change)
        if reason is not None:
            raise ValueError(f"ARCH_PATH_VIOLATION {change.path}: {reason}")


def parse_name_status_z(data: str) -> list[Change]:
    fields = [field for field in data.split("\0") if field]
    changes: list[Change] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if status.startswith("R"):
            if index + 1 >= len(fields):
                raise ValueError("ARCH_PATH_VIOLATION <diff>: malformed rename record")
            old_path, path = fields[index], fields[index + 1]
            index += 2
            changes.append(Change("R", path, old_path=old_path))
        elif status == "A":
            if index >= len(fields):
                raise ValueError("ARCH_PATH_VIOLATION <diff>: malformed add record")
            changes.append(Change("A", fields[index]))
            index += 1
        elif status in {"M", "D", "T"}:
            if index >= len(fields):
                raise ValueError("ARCH_PATH_VIOLATION <diff>: malformed change record")
            index += 1
        else:
            raise ValueError(f"ARCH_PATH_VIOLATION <diff>: unsupported git status {status!r}")
    return changes


def main() -> int:
    try:
        validate_changes(parse_name_status_z(sys.stdin.read()))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
