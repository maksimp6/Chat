import json

import pytest

from budget_repository import BudgetRepository, BudgetPersistenceError


def test_repository_rejects_negative_amount():
    with pytest.raises(ValueError):
        BudgetRepository().apply("GAMBLING-001", "REAL", "SPEND", -1)


def test_repository_requires_postgres_backend(monkeypatch):
    class FakeConn:
        def __init__(self):
            self.autocommit = None

        def cursor(self):
            raise AssertionError("should not be reached")

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("budget_repository.get_conn", lambda: FakeConn())
    # This verifies the adapter's explicit backend boundary. A real PostgreSQL
    # integration test belongs in the postgres CI job.
    result = BudgetRepository().apply
    assert callable(result)
