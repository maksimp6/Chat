"""RED contract for documentation ↔ executable-test evidence (#978).

The checker is intentionally absent in this slice. These tests define the public
validation contract before implementation.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.check_doc_test_contracts import validate_contract_registry


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _registry(root: Path, contracts: list[dict[str, object]]) -> Path:
    path = root / "docs" / "contracts.json"
    _write(path, json.dumps({"contracts": contracts}))
    return path


def _contract(**overrides: object) -> dict[str, object]:
    contract: dict[str, object] = {
        "id": "AUTH-EXAMPLE",
        "status": "current",
        "documentation": "docs/auth.md",
        "implementation": ["identity/auth.py"],
        "tests": ["tests/test_auth.py"],
        "facts": {"mode": "required"},
    }
    contract.update(overrides)
    return contract


def test_rejects_current_documented_contract_without_proving_test(tmp_path: Path) -> None:
    _write(tmp_path / "docs/auth.md", "# Auth\n")
    _write(tmp_path / "identity/auth.py")
    registry = _registry(tmp_path, [_contract(tests=[])])

    errors = validate_contract_registry(tmp_path, registry)

    assert "DOC_WITHOUT_TEST AUTH-EXAMPLE" in errors


def test_rejects_tracked_test_contract_without_current_documentation(tmp_path: Path) -> None:
    _write(tmp_path / "identity/auth.py")
    _write(tmp_path / "tests/test_auth.py")
    registry = _registry(tmp_path, [_contract(documentation="")])

    errors = validate_contract_registry(tmp_path, registry)

    assert "TEST_WITHOUT_DOC AUTH-EXAMPLE" in errors


def test_rejects_machine_comparable_doc_test_fact_conflict(tmp_path: Path) -> None:
    _write(tmp_path / "docs/auth.md", "# Auth\n")
    _write(tmp_path / "identity/auth.py")
    _write(
        tmp_path / "tests/test_auth.py",
        'DOC_CONTRACT_FACTS = {"mode": "optional"}\n',
    )
    registry = _registry(tmp_path, [_contract()])

    errors = validate_contract_registry(tmp_path, registry)

    assert "DOC_TEST_CONFLICT AUTH-EXAMPLE mode" in errors


def test_accepts_consistent_current_contract(tmp_path: Path) -> None:
    _write(tmp_path / "docs/auth.md", "# Auth\n")
    _write(tmp_path / "identity/auth.py")
    _write(
        tmp_path / "tests/test_auth.py",
        'DOC_CONTRACT_FACTS = {"mode": "required"}\n',
    )
    registry = _registry(tmp_path, [_contract()])

    assert validate_contract_registry(tmp_path, registry) == []


def test_planned_contract_does_not_require_shipped_test_evidence(tmp_path: Path) -> None:
    _write(tmp_path / "docs/future.md", "# Future\n")
    registry = _registry(
        tmp_path,
        [
            _contract(
                id="FUTURE-EXAMPLE",
                status="planned",
                documentation="docs/future.md",
                implementation=[],
                tests=[],
                facts={},
            )
        ],
    )

    assert validate_contract_registry(tmp_path, registry) == []


def test_rejects_missing_paths_duplicate_ids_and_invalid_lifecycle(tmp_path: Path) -> None:
    registry = _registry(
        tmp_path,
        [
            _contract(status="banana"),
            _contract(),
        ],
    )

    errors = validate_contract_registry(tmp_path, registry)

    assert "INVALID_LIFECYCLE AUTH-EXAMPLE banana" in errors
    assert "DUPLICATE_CONTRACT_ID AUTH-EXAMPLE" in errors
    assert "MISSING_PATH AUTH-EXAMPLE docs/auth.md" in errors
    assert "MISSING_PATH AUTH-EXAMPLE identity/auth.py" in errors
    assert "MISSING_PATH AUTH-EXAMPLE tests/test_auth.py" in errors


def test_unregistered_private_unit_test_is_outside_contract_registry(tmp_path: Path) -> None:
    _write(tmp_path / "tests/test_private_helper.py", "def test_helper(): assert True\n")
    registry = _registry(tmp_path, [])

    assert validate_contract_registry(tmp_path, registry) == []
