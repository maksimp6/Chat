import pytest

from budget_repository import BudgetPersistenceError, BudgetRepository


def test_repository_rejects_negative_amount():
    with pytest.raises(ValueError):
        BudgetRepository().apply("GAMBLING-001", "REAL", "SPEND", -1)


def test_repository_requires_postgres_configuration(monkeypatch):
    monkeypatch.setattr("budget_repository.is_postgres_configured", lambda: False)

    class FakeConn:
        def cursor(self):
            raise AssertionError("PostgreSQL cursor must not be touched")

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("budget_repository.get_conn", lambda: FakeConn())

    with pytest.raises(BudgetPersistenceError, match="PostgreSQL backend"):
        BudgetRepository().apply("GAMBLING-001", "REAL", "SPEND", 1)
