#!/usr/bin/env python3
"""Validate mappings between canonical docs, implementation and proving tests."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

ALLOWED_STATUS = {"current", "experimental", "planned", "historical", "retired"}
PROVEN_STATUS = {"current"}


def validate_contracts(root: Path, contracts: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for entry in contracts:
        contract_id = entry.get("id")
        if not isinstance(contract_id, str) or not contract_id:
            errors.append("contract: missing id")
            continue
        if contract_id in seen:
            errors.append(f"{contract_id}: duplicate contract id")
        seen.add(contract_id)

        status = entry.get("status")
        if status not in ALLOWED_STATUS:
            errors.append(f"{contract_id}: invalid status: {status}")
            continue

        doc = entry.get("doc")
        if not isinstance(doc, str) or not (root / doc).is_file():
            errors.append(f"{contract_id}: missing doc: {doc}")

        implementation = entry.get("implementation", [])
        tests = entry.get("tests", [])
        if status in PROVEN_STATUS and not tests:
            errors.append(f"{contract_id}: DOC_WITHOUT_TEST")
        if status in PROVEN_STATUS and not implementation:
            errors.append(f"{contract_id}: current contract has no implementation evidence")

        for path in implementation:
            if not (root / path).is_file():
                errors.append(f"{contract_id}: missing implementation: {path}")
        for path in tests:
            if not (root / path).is_file():
                errors.append(f"{contract_id}: missing test: {path}")
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
