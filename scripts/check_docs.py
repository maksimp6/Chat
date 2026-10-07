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
    return sorted(path.relative_to(root).as_posix() for path in docs_root.rglob("*.md") if path.is_file())


def _missing_fields(entry: dict[str, Any]) -> list[str]:
    return sorted(REQUIRED_FIELDS - set(entry))


def _valid_path(path: object) -> bool:
    return isinstance(path, str) and path.startswith("docs/") and path.endswith(".md")


def _validate_metadata(path: str, entry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
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
    return errors


def _validate_entry(
    root: Path,
    entry: object,
    index: int,
    seen: set[str],
) -> tuple[list[str], str | None]:
    prefix = f"documents[{index}]"
    if not isinstance(entry, dict):
        return [f"{prefix}: entry must be an object"], None

    missing = _missing_fields(entry)
    if missing:
        return [f"{prefix}: missing fields: {', '.join(missing)}"], None

    path = entry["path"]
    if not _valid_path(path):
        return [f"{prefix}: invalid path"], None

    errors = _validate_metadata(path, entry)
    if path in seen:
        errors.append(f"{prefix}: duplicate path: {path}")
    if not (root / path).is_file():
        errors.append(f"{path}: catalog entry points to a missing file")
    return errors, path


def _coverage_errors(discovered: set[str], catalog_paths: set[str]) -> list[str]:
    errors = [
        f"{path}: missing from docs/catalog.json"
        for path in sorted(discovered - catalog_paths)
    ]
    errors.extend(
        f"{path}: catalog entry has no Markdown file"
        for path in sorted(catalog_paths - discovered)
    )
    return errors


def validate_catalog(root: Path, catalog: dict[str, Any]) -> list[str]:
    entries = catalog.get("documents")
    if not isinstance(entries, list):
        return ["catalog.documents must be a list"]

    errors: list[str] = []
    seen: set[str] = set()
    catalog_paths: set[str] = set()

    for index, entry in enumerate(entries):
        entry_errors, path = _validate_entry(root, entry, index, seen)
        errors.extend(entry_errors)
        if path is not None:
            seen.add(path)
            catalog_paths.add(path)

    errors.extend(_coverage_errors(set(discover_docs(root)), catalog_paths))
    return errors


def _load_catalog(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("catalog root must be an object")
    return data


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    catalog_path = root / "docs" / "catalog.json"
    try:
        catalog = _load_catalog(catalog_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
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
