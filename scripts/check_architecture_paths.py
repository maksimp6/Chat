#!/usr/bin/env python3
"""Fail fast when added or renamed files violate proven repository boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import sys


PROJECT_ROOT_PYTHON = {"app.py", "config.py"}
LEGACY_ROOT_PYTHON = {
    "alice_agent_runner.py",
    "api_contracts.py",
    "archiver.py",
    "credential_crypto.py",
    "db.py",
    "key_manager.py",
    "mcp_routes.py",
    "memory_db.py",
    "memory_extractor.py",
    "memory_manager.py",
    "model_discovery.py",
    "partial_output.py",
    "runtime_api.py",
    "runtime_tools.py",
    "sdk.py",
    "storage.py",
    "tool_registry.py",
    "universal_tool_platform.py",
    "voice_routes.py",
}
APPROVED_ROOT_PYTHON = PROJECT_ROOT_PYTHON | LEGACY_ROOT_PYTHON
PRUNED_ROOT_MODULES = {
    "agent_context.py",
    "agent_runner.py",
    "agent_tools.py",
    "run_agent.py",
    "run_agent_loop.py",
    "yandex_agent_loop.py",
}
MOVED_ROOT_MODULES = {
    "invocation_api.py",
    "invocation_context.py",
    "invocation_manager.py",
    "invocation_trace.py",
    "browser_adapters.py",
    "browser_capabilities.py",
    "termux_mcp_tools.py",
    "termux_system_tools.py",
    "filesystem_mcp_tools.py",
    "git_mcp_tools.py",
    "wikipedia_mcp_tools.py",
    "profiler_tools.py",
    "theme_tools.py",
}


@dataclass(frozen=True)
class Change:
    status: str
    path: str
    old_path: str | None = None


def _root_module_violation(name: str) -> str | None:
    if name in PRUNED_ROOT_MODULES:
        return "pruned legacy agent module must stay deleted"
    if name in MOVED_ROOT_MODULES:
        return "module already belongs to its canonical package"
    if name not in APPROVED_ROOT_PYTHON:
        return "new Python implementation modules must live in a package"
    return None


def _violation(change: Change) -> str | None:
    path = PurePosixPath(change.path)
    reason = None
    if change.status not in {"A", "R"}:
        reason = f"unsupported change status {change.status!r}"
    elif path.is_absolute() or ".." in path.parts:
        reason = "path escapes repository root"
    elif path.parts and path.parts[0] == "docs" and path.suffix == ".py":
        reason = "Python implementation files do not belong under docs/"
    elif len(path.parts) == 1 and path.suffix == ".py":
        reason = _root_module_violation(path.name)
    return reason


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
