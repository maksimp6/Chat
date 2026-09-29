import os

import pytest

from conftest import _require_disposable_postgres_target


def test_postgres_reset_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("ALICE_PYTEST_POSTGRES_RESET", raising=False)

    with pytest.raises(RuntimeError, match="ALICE_PYTEST_POSTGRES_RESET=1"):
        _require_disposable_postgres_target(
            "postgresql://alice:secret@localhost:5432/alice_pro_test"
        )


def test_postgres_reset_rejects_non_test_database(monkeypatch):
    monkeypatch.setenv("ALICE_PYTEST_POSTGRES_RESET", "1")

    with pytest.raises(RuntimeError, match="non-test database"):
        _require_disposable_postgres_target(
            "postgresql://alice:secret@localhost:5432/alice_pro"
        )


def test_postgres_reset_accepts_explicit_disposable_test_database(monkeypatch):
    monkeypatch.setenv("ALICE_PYTEST_POSTGRES_RESET", "1")

    _require_disposable_postgres_target(
        "postgresql://alice:secret@localhost:5432/alice_pro_test"
    )
