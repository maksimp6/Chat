import json

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


def test_repository_uses_adapter_placeholders_and_returns_result(monkeypatch):
    class FakeCursor:
        def __init__(self):
            self.sql = None
            self.params = None

        def execute(self, sql, params):
            self.sql = sql
            self.params = tuple(params)

        def fetchone(self):
            return (json.dumps({"operation_id": "op-1", "status": "APPLIED"}),)

    class FakeConn:
        def __init__(self):
            self.cur = FakeCursor()
            self.committed = False

        def cursor(self):
            return self.cur

        def commit(self):
            self.committed = True

        def rollback(self):
            raise AssertionError("rollback must not be called")

        def close(self):
            pass

    conn = FakeConn()
    monkeypatch.setattr("budget_repository.is_postgres_configured", lambda: True)
    monkeypatch.setattr("budget_repository.get_conn", lambda: conn)

    result = BudgetRepository().apply(
        "GAMBLING-001",
        "REAL",
        "RESERVE",
        "10.00",
        idempotency_key="issue8-test-1",
        actor="controller-test",
    )

    assert result == {"operation_id": "op-1", "status": "APPLIED"}
    assert conn.committed is True
    assert "?, ?, ?, ?, ?, ?, ?" in conn.cur.sql
    assert conn.cur.params[0:3] == ("GAMBLING-001", "REAL", "RESERVE")
    assert str(conn.cur.params[3]) == "10.00"


def test_repository_emits_execution_trace_event(monkeypatch):
    events = []

    class FakeTrace:
        def add_event(self, event_type, payload):
            events.append((event_type, payload))

    class FakeCursor:
        def execute(self, sql, params):
            pass

        def fetchone(self):
            return ({"operation_id": "op-2", "status": "APPLIED"},)

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("budget_repository.is_postgres_configured", lambda: True)
    monkeypatch.setattr("budget_repository.get_conn", lambda: FakeConn())
    monkeypatch.setattr("budget_repository.get_current_trace", lambda: FakeTrace())

    BudgetRepository().apply(
        "GAMBLING-001", "DEMO", "WIN", "5", actor="provider"
    )

    assert events
    event_type, payload = events[-1]
    assert event_type == "budget_operation_persisted"
    assert payload["account_type"] == "DEMO"
    assert payload["operation_type"] == "WIN"
    assert payload["amount"] == "5"
