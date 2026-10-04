#!/usr/bin/env python3
"""Print the deterministic Alice Pro CI environment content hash."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = (
    "deploy/ci/Dockerfile",
    "requirements.txt",
    "requirements-dev.txt",
    "package.json",
)


def main() -> None:
    digest = hashlib.sha256()
    for relative in INPUTS:
        path = ROOT / relative
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    print(digest.hexdigest()[:20])


if __name__ == "__main__":
    main()
