"""Contract tests for the changed-function quality ratchet (#997)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_changed_types.py"


def _load_checker():
    assert CHECKER.exists(), "GREEN #997 requires scripts/check_changed_types.py"
    spec = importlib.util.spec_from_file_location("check_changed_types", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("source", "expected_rule"),
    [
        ('def f(value: str):\n    """Return value."""\n    return value\n', "missing-return"),
        (
            'def f(value) -> str:\n    """Return value."""\n    return str(value)\n',
            "missing-parameter",
        ),
        ('def f() -> dict:\n    """Return mapping."""\n    return {}\n', "unparameterized-generic"),
        (
            'from typing import Any\ndef f(value: Any) -> Any:\n    """Return value."""\n    return value\n',
            "any-leak",
        ),
        (
            'def f(value: object) -> str:\n    """Read name."""\n    return value.name  # type: ignore\n',
            "type-ignore-policy",
        ),
        (
            'def f(value: object) -> str:\n    """Read name."""\n    return value.name  # type: ignore[attr-defined]\n',
            "type-ignore-policy",
        ),
        (
            'from typing import List\ndef f() -> List:\n    """Return values."""\n    return []\n',
            "unparameterized-generic",
        ),
        ("def f(value: str) -> str:\n    return value\n", "missing-docstring"),
    ],
)
def test_rejects_new_function_contract_debt(source: str, expected_rule: str) -> None:
    checker = _load_checker()
    violations = checker.check_source("new_module.py", source, is_new=True)
    assert expected_rule in {item.rule for item in violations}


@pytest.mark.parametrize(
    "source",
    [
        'def f(value: str) -> int:\n    """Return length."""\n    return len(value)\n',
        'async def f(value: str) -> int:\n    """Return length."""\n    return len(value)\n',
        (
            "from typing import Protocol\n"
            "class Reader(Protocol):\n"
            "    def read(self, key: str) -> bytes: ...\n"
            "class Impl:\n"
            "    def read(self, key: str) -> bytes:\n"
            '        """Read one value."""\n'
            "        return key.encode()\n"
        ),
        "def _helper(value: str) -> str:\n    return value\n",
        (
            "def f(value: object) -> str:\n"
            '    """Read name."""\n'
            "    return value.name  # type: ignore[attr-defined]  # validated adapter\n"
        ),
    ],
)
def test_accepts_precisely_typed_contracts(source: str) -> None:
    checker = _load_checker()
    assert checker.check_source("new_module.py", source, is_new=True) == []


def test_untouched_legacy_debt_does_not_block_clean_addition() -> None:
    checker = _load_checker()
    before = "def legacy(value):\n    return value\n"
    after = before + '\n\ndef clean(value: str) -> str:\n    """Return value."""\n    return value\n'
    assert checker.check_changed_source("module.py", before, after) == []


def test_changed_legacy_function_must_meet_forward_contract() -> None:
    checker = _load_checker()
    before = "def legacy(value):\n    return value\n"
    after = "def legacy(value):\n    result = value\n    return result\n"
    rules = {item.rule for item in checker.check_changed_source("module.py", before, after)}
    assert {"missing-parameter", "missing-return", "missing-docstring"} <= rules


def test_rename_without_function_changes_does_not_create_debt() -> None:
    checker = _load_checker()
    source = "def legacy(value):\n    return value\n"
    assert checker.check_changed_source("renamed.py", source, source) == []
