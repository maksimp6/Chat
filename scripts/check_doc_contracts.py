#!/usr/bin/env python3
"""Validate mappings between canonical docs, implementation and proving tests."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

ALLOWED_STATUS = {"current", "experimental", "planned", "historical", "retired"}
PROVEN_STATUS = {"current"}


def _validate_paths(
    root: Path,
    contract_id: str,
    label: str,
    paths: list[str],
) -> list[str]:
    return [
        f"{contract_id}: missing {label}: {path}"
        for path in paths
        if not (root / path).is_file()
    ]


def _validate_entry(root: Path, entry: dict[str, Any]) -> tuple[str | None, list[str]]:
    contract_id = entry.get("id")
    if not isinstance(contract_id, str) or not contract_id:
        return None, ["contract: missing id"]

    status = entry.get("status")
    if status not in ALLOWED_STATUS:
        return contract_id, [f"{contract_id}: invalid status: {status}"]

    errors: list[str] = []
    doc = entry.get("doc")
    if not isinstance(doc, str) or not (root / doc).is_file():
        errors.append(f"{contract_id}: missing doc: {doc}")

    implementation = entry.get("implementation", [])
    tests = entry.get("tests", [])
    if status in PROVEN_STATUS and not tests:
        errors.append(f"{contract_id}: DOC_WITHOUT_TEST")
    if status in PROVEN_STATUS and not implementation:
        errors.append(f"{contract_id}: current contract has no implementation evidence")
    errors.extend(_validate_paths(root, contract_id, "implementation", implementation))
    errors.extend(_validate_paths(root, contract_id, "test", tests))
    return contract_id, errors


def _duplicate_id_errors(contracts: list[dict[str, Any]]) -> list[str]:
    ids = [entry.get("id") for entry in contracts if isinstance(entry.get("id"), str)]
    return [
        f"{contract_id}: duplicate contract id"
        for contract_id in sorted(set(ids))
        if ids.count(contract_id) > 1
    ]


def validate_contracts(root: Path, contracts: list[dict[str, Any]]) -> list[str]:
    errors = _duplicate_id_errors(contracts)
    for entry in contracts:
        _contract_id, entry_errors = _validate_entry(root, entry)
        errors.extend(entry_errors)
    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    path = root / "docs" / "contracts.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    contracts = data.get("contracts", [])
    errors = validate_contracts(root, contracts)
    if errors:
        for error in errors:
            print(f"docs-contract: {error}", file=sys.stderr)
        return 1
    print(f"Documentation evidence registry valid: {len(contracts)} contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
