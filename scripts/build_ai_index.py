#!/usr/bin/env python3
"""Compatibility entrypoint for the repository-index CLI."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repository_index.builder import *  # noqa: F403
from repository_index.builder import main


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError):
        print(
            "Repository index failed: invalid input, unavailable revision, incompatible cache, limit or I/O error.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
