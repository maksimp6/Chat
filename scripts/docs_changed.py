#!/usr/bin/env python3
"""Filter repository-relative changed paths to documentation Markdown."""

from __future__ import annotations

import sys
from pathlib import PurePosixPath


def is_documentation_markdown(path: str) -> bool:
    if not path or path.startswith("/") or ".." in PurePosixPath(path).parts:
        return False
    return path.endswith(".md") and (
        "/" not in path or path.startswith(("docs/", ".github/ISSUE_TEMPLATE/", ".agents/"))
    )


def main() -> int:
    paths = [path for path in sys.stdin.read().split("\0") if path]
    selected = [path for path in paths if is_documentation_markdown(path)]
    if not selected:
        return 1
    sys.stdout.write("\0".join(selected) + "\0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
