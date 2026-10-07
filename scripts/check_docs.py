#!/usr/bin/env python3
"""Validate the Alice Pro documentation catalog."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

ALLOWED_KINDS = {
    "tutorial",
    "how-to",
    "runbook",
    "reference",
    "explanation",
    "decision",
    "historical",
    "index",
}
ALLOWED_STATUSES = {
    "current",
    "experimental",
    "planned",
    "historical",
    "retired",
}
REQUIRED_FIELDS = {
    "path",
    "kind",
    "status",
    "owner",
    "source_of_truth",
    "canonical",
}


def discover_docs(root: Path) -> list[str]:
    docs_root = root / "docs"
    return sorted(
        path.relative_to(root).as_posix()
        for path in docs_root.rglob("*.md")
        if path.is_file()
    )


def validate_catalog(root: Path, catalog: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    entries = catalog.get("documents")
    if not isinstance(entries, list):
        return ["catalog.documents must be a list"]

    seen: set[str] = set()
    catalog_paths: set[str] = set()
    for index, entry in enumerate(entries):
        prefix = f"documents[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix}: entry must be an object")
            continue
        missing = sorted(REQUIRED_FIELDS - set(entry))
        if missing:
            errors.append(f"{prefix}: missing fields: {', '.join(missing)}")
            continue

        path = entry["path"]
        if not isinstance(path, str) or not path.startswith("docs/") or not path.endswith(".md"):
            errors.append(f"{prefix}: invalid path")
            continue
        if path in seen:
            errors.append(f"{prefix}: duplicate path: {path}")
        seen.add(path)
        catalog_paths.add(path)

        if entry["kind"] not in ALLOWED_KINDS:
            errors.append(f"{path}: invalid kind: {entry['kind']}")
        if entry["status"] not in ALLOWED_STATUSES:
            errors.append(f"{path}: invalid status: {entry['status']}")
        if not isinstance(entry["owner"], str) or not entry["owner"].strip():
            errors.append(f"{path}: owner is required")
        if not isinstance(entry["source_of_truth"], str) or not entry["source_of_truth"].strip():
            errors.append(f"{path}: source_of_truth is required")
        if not isinstance(entry["canonical"], bool):
            errors.append(f"{path}: canonical must be boolean")
        if entry["status"] == "retired" and entry["canonical"] is not False:
            errors.append(f"{path}: retired documents cannot be canonical")

        full_path = root / path
        if not full_path.is_file():
            errors.append(f"{path}: catalog entry points to a missing file")

    discovered = set(discover_docs(root))
    for path in sorted(discovered - catalog_paths):
        errors.append(f"{path}: missing from docs/catalog.json")
    for path in sorted(catalog_paths - discovered):
        errors.append(f"{path}: catalog entry has no Markdown file")
    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    catalog_path = root / "docs" / "catalog.json"
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"documentation catalog error: {exc}", file=sys.stderr)
        return 1

    errors = validate_catalog(root, catalog)
    if errors:
        for error in errors:
            print(f"docs: {error}", file=sys.stderr)
        return 1
    print(f"Documentation catalog valid: {len(catalog['documents'])} Markdown files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
