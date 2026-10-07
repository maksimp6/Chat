#!/usr/bin/env python3
"""Detect changes that are documentation-only.

The scope is intentionally narrower than the general platform router because
security/static-analysis fast paths must fail closed for code, workflow and
agent-definition changes.
"""

from __future__ import annotations

from pathlib import PurePosixPath
import sys

DOC_PREFIXES = ("docs/", ".github/ISSUE_TEMPLATE/")


def _is_documentation_path(path: str) -> bool:
    if not path or path.startswith("/") or ".." in PurePosixPath(path).parts:
        return False
    return path.startswith(DOC_PREFIXES) or path.endswith(".md")


def is_docs_only(paths: list[str]) -> bool:
    return bool(paths) and all(_is_documentation_path(path) for path in paths)


def main() -> int:
    args = sys.argv[1:]
    if args not in ([], ["--null"]):
        print("Usage: ci_docs_only.py [--null]", file=sys.stderr)
        return 2
    data = sys.stdin.read()
    paths = data.split("\0") if args else data.splitlines()
    print("true" if is_docs_only([path for path in paths if path]) else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
