#!/usr/bin/env python3
"""Reject tracked paths with whitespace, control characters or non-ASCII text."""

from __future__ import annotations

import re
import subprocess
import sys

# ASCII letters, digits, dot, underscore, hyphen and slash only.
ALLOWED_PATH = re.compile(r"^[A-Za-z0-9._/-]+$")


def find_bad_paths(paths: list[str]) -> list[str]:
    return [path for path in paths if not ALLOWED_PATH.fullmatch(path)]


def tracked_paths() -> list[str]:
    output = subprocess.run(
        ["git", "ls-files", "-z"], check=True, capture_output=True, text=True
    ).stdout
    return [path for path in output.split("\0") if path]


def main() -> int:
    bad = find_bad_paths(tracked_paths())
    for path in bad:
        print(
            f"FILENAME_VIOLATION {path!r}: only ASCII letters, digits, '.', '_', '-' and '/' are allowed",
            file=sys.stderr,
        )
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
