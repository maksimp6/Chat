#!/usr/bin/env python3
"""Validate documentation ↔ executable-test contract evidence."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import sys
from typing import Any

VALID_LIFECYCLES = {"current", "experimental", "planned", "historical", "retired"}
SHIPPED_LIFECYCLES = {"current"}
FACTS_NAME = "DOC_CONTRACT_FACTS"


def _load_test_facts(path: Path) -> dict[str, object] | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == FACTS_NAME for target in targets):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            return None
        return value if isinstance(value, dict) else None
    return None


def _paths(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def validate_contract_registry(root: Path, registry_path: Path) -> list[str]:
    errors: list[str] = []
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"INVALID_REGISTRY {exc}"]

    contracts = payload.get("contracts") if isinstance(payload, dict) else None
    if not isinstance(contracts, list):
        return ["INVALID_REGISTRY contracts"]

    seen: set[str] = set()
    for raw in contracts:
        if not isinstance(raw, dict):
            errors.append("INVALID_CONTRACT <non-object>")
            continue

        contract_id = raw.get("id")
        if not isinstance(contract_id, str) or not contract_id:
            errors.append("INVALID_CONTRACT_ID")
            continue

        if contract_id in seen:
            errors.append(f"DUPLICATE_CONTRACT_ID {contract_id}")
        seen.add(contract_id)

        status = raw.get("status")
        if status not in VALID_LIFECYCLES:
            errors.append(f"INVALID_LIFECYCLE {contract_id} {status}")

        documentation = raw.get("documentation")
        implementation = _paths(raw.get("implementation"))
        tests = _paths(raw.get("tests"))

        tracked_paths: list[str] = []
        if isinstance(documentation, str) and documentation:
            tracked_paths.append(documentation)
        tracked_paths.extend(implementation)
        tracked_paths.extend(tests)
        for relative in tracked_paths:
            if not (root / relative).is_file():
                errors.append(f"MISSING_PATH {contract_id} {relative}")

        if status in SHIPPED_LIFECYCLES:
            if not tests:
                errors.append(f"DOC_WITHOUT_TEST {contract_id}")
            if tests and (not isinstance(documentation, str) or not documentation):
                errors.append(f"TEST_WITHOUT_DOC {contract_id}")

        expected_facts = raw.get("facts")
        if (
            status in SHIPPED_LIFECYCLES
            and isinstance(expected_facts, dict)
            and expected_facts
        ):
            for test_path in tests:
                path = root / test_path
                if not path.is_file():
                    continue
                actual = _load_test_facts(path)
                if actual is None:
                    continue
                for key, expected in expected_facts.items():
                    if key in actual and actual[key] != expected:
                        errors.append(f"DOC_TEST_CONFLICT {contract_id} {key}")

    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    registry = root / "docs" / "contracts.json"
    if not registry.is_file():
        print("Documentation-test contract registry not present; nothing to validate")
        return 0
    errors = validate_contract_registry(root, registry)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("Documentation-test contracts valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
