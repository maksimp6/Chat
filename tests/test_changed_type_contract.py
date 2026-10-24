"""RED contract tests for the changed-code typing ratchet (#997).

These tests intentionally describe behavior that the implementation does not
provide yet. Keep this PR RED until the checker exists.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_changed_types.py"


def _load_checker():
    assert CHECKER.exists(), "RED #997: scripts/check_changed_types.py is not implemented"
    spec = importlib.util.spec_from_file_location("check_changed_types", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("source", "expected_rule"),
    [
        ("def f(value: str):\n    return value\n", "missing-return"),
        ("def f(value) -> str:\n    return str(value)\n", "missing-parameter"),
        ("def f() -> dict:\n    return {}\n", "unparameterized-generic"),
        (
            "from typing import Any\ndef f(value: Any) -> Any:\n    return value\n",
            "any-leak",
        ),
        (
            "def f(value: object) -> str:\n    return value.name  # type: ignore\n",
            "type-ignore-policy",
        ),
    ],
)
def test_rejects_new_typing_debt(source: str, expected_rule: str) -> None:
    checker = _load_checker()
    violations = checker.check_source("new_module.py", source, is_new=True)
    assert expected_rule in {item.rule for item in violations}


def test_new_module_must_be_strict_clean() -> None:
    checker = _load_checker()
    source = "def f(value) -> str:\n    return str(value)\n"
    violations = checker.check_source("new_module.py", source, is_new=True)
    assert violations


@pytest.mark.parametrize(
    "source",
    [
        "def f(value: str) -> int:\n    return len(value)\n",
        "async def f(value: str) -> int:\n    return len(value)\n",
        (
            "from typing import Protocol\n"
            "class Reader(Protocol):\n"
            "    def read(self, key: str) -> bytes: ...\n"
            "class Impl:\n"
            "    def read(self, key: str) -> bytes:\n"
            "        return key.encode()\n"
        ),
    ],
)
def test_accepts_precisely_typed_contracts(source: str) -> None:
    checker = _load_checker()
    assert checker.check_source("new_module.py", source, is_new=True) == []
