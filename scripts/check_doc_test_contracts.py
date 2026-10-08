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
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _contract_id(raw: dict[str, Any]) -> str | None:
    value = raw.get("id")
    return value if isinstance(value, str) and value else None


def _validate_identity(
    contract_id: str, status: object, seen: set[str]
) -> list[str]:
    errors: list[str] = []
    if contract_id in seen:
        errors.append(f"DUPLICATE_CONTRACT_ID {contract_id}")
    seen.add(contract_id)
    if status not in VALID_LIFECYCLES:
        errors.append(f"INVALID_LIFECYCLE {contract_id} {status}")
    return errors


def _validate_paths(
    root: Path, contract_id: str, documentation: object, implementation: list[str], tests: list[str]
) -> list[str]:
    tracked = [documentation] if isinstance(documentation, str) and documentation else []
    tracked.extend(implementation)
    tracked.extend(tests)
    return [
        f"MISSING_PATH {contract_id} {relative}"
        for relative in tracked
        if not (root / relative).is_file()
    ]


def _validate_shipped_mapping(
    contract_id: str, status: object, documentation: object, tests: list[str]
) -> list[str]:
    if status not in SHIPPED_LIFECYCLES:
        return []
    errors: list[str] = []
    if not tests:
        errors.append(f"DOC_WITHOUT_TEST {contract_id}")
    if tests and (not isinstance(documentation, str) or not documentation):
        errors.append(f"TEST_WITHOUT_DOC {contract_id}")
    return errors


def _validate_facts(
    root: Path,
    contract_id: str,
    status: object,
    tests: list[str],
    expected_facts: object,
) -> list[str]:
    if status not in SHIPPED_LIFECYCLES or not isinstance(expected_facts, dict):
        return []
    errors: list[str] = []
    for test_path in tests:
        path = root / test_path
        if not path.is_file():
            continue
        actual = _load_test_facts(path)
        if actual is None:
            continue
        errors.extend(
            f"DOC_TEST_CONFLICT {contract_id} {key}"
            for key, expected in expected_facts.items()
            if key in actual and actual[key] != expected
        )
    return errors


def _validate_contract(
    root: Path, raw: object, seen: set[str]
) -> list[str]:
    if not isinstance(raw, dict):
        return ["INVALID_CONTRACT <non-object>"]
    contract_id = _contract_id(raw)
    if contract_id is None:
        return ["INVALID_CONTRACT_ID"]

    status = raw.get("status")
    documentation = raw.get("documentation")
    implementation = _paths(raw.get("implementation"))
    tests = _paths(raw.get("tests"))

    errors = _validate_identity(contract_id, status, seen)
    errors.extend(_validate_paths(root, contract_id, documentation, implementation, tests))
    errors.extend(_validate_shipped_mapping(contract_id, status, documentation, tests))
    errors.extend(_validate_facts(root, contract_id, status, tests, raw.get("facts")))
    return errors


def validate_contract_registry(root: Path, registry_path: Path) -> list[str]:
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"INVALID_REGISTRY {exc}"]

    contracts = payload.get("contracts") if isinstance(payload, dict) else None
    if not isinstance(contracts, list):
        return ["INVALID_REGISTRY contracts"]

    seen: set[str] = set()
    errors: list[str] = []
    for raw in contracts:
        errors.extend(_validate_contract(root, raw, seen))
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
