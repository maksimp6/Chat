#!/usr/bin/env python3
"""Keep non-current documentation out of current docs navigation."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote

HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
NONCURRENT = {"planned", "historical", "retired"}
HISTORICAL_HEADING = "historical / retired / not planned"


def _catalog_map(data: dict) -> dict[str, dict]:
    return {
        entry["path"]: entry
        for entry in data.get("documents", [])
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    }


def _target(destination: str) -> str | None:
    value = unquote(destination.strip().split(maxsplit=1)[0])
    if value.startswith(("http://", "https://", "mailto:", "tel:", "#", "/")):
        return None
    value = value.split("#", 1)[0].split("?", 1)[0]
    if not value:
        return None
    return "docs/" + value.removeprefix("./")


def validate_navigation(text: str, catalog: dict[str, dict]) -> list[str]:
    errors: list[str] = []
    section = ""
    for line in text.splitlines():
        heading = HEADING_RE.match(line)
        if heading:
            section = heading.group(1).strip().lower()
            continue
        if section == HISTORICAL_HEADING:
            continue
        for raw in LINK_RE.findall(line):
            target = _target(raw)
            if target is None or target not in catalog:
                continue
            status = catalog[target].get("status")
            if status in NONCURRENT:
                errors.append(
                    f"{target}: {status} document linked from current navigation section"
                )
    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads((root / "docs" / "catalog.json").read_text(encoding="utf-8"))
    index = (root / "docs" / "README.md").read_text(encoding="utf-8")
    errors = validate_navigation(index, _catalog_map(catalog))
    if errors:
        for error in errors:
            print(f"docs-nav: {error}", file=sys.stderr)
        return 1
    print("Documentation navigation lifecycle valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
