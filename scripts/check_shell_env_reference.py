#!/usr/bin/env python3
"""Discover externally supplied uppercase environment reads in shell scripts."""

from __future__ import annotations

from pathlib import Path
import re

READ_RE = re.compile(r"\$(?:\{([A-Z][A-Z0-9_]*)[^}]*\}|([A-Z][A-Z0-9_]*))")
ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)=")


def discover_external_shell_env(text: str) -> set[str]:
    assigned = {
        match.group(1)
        for line in text.splitlines()
        if (match := ASSIGN_RE.match(line))
    }
    reads: set[str] = set()
    for first, second in READ_RE.findall(text):
        name = first or second
        if name not in assigned:
            reads.add(name)
    return reads


def discover_shell_files(root: Path) -> list[Path]:
    files = list((root / "scripts").glob("*.sh"))
    files.extend((root / "deploy").rglob("*.sh"))
    return sorted(path for path in files if path.is_file())


def discover_repository_shell_env(root: Path) -> set[str]:
    keys: set[str] = set()
    for path in discover_shell_files(root):
        keys.update(discover_external_shell_env(path.read_text(encoding="utf-8")))
    return keys
