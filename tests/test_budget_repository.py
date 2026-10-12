"""Native financial mutations, idempotency and restart under one write barrier."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import pytest
import db
from agent_memory.client import close_memory_clients
from budget_repository import BudgetPersistenceError, BudgetRepository
from budget_controller import AccountType, BudgetAccount, BudgetLimits


def account(kind=AccountType.REAL, allocated="100"):
    return BudgetAccount(
        kind,
        "RUB",
        allocated=Decimal(allocated),
        limits=BudgetLimits(Decimal("100"), Decimal("100"), Decimal("100")),
    )


def repository():
    repo = BudgetRepository()
    repo.configure("budget", account())
    repo.configure("budget", account(AccountType.DEMO))
    return repo


def test_repository_rejects_negative_amount():
    with pytest.raises(ValueError):
        repository().apply("budget", "REAL", "SPEND", -1)


def test_repository_requires_initialized_account_and_authorized_allocation():
    repo = BudgetRepository()
    with pytest.raises(BudgetPersistenceError, match="account not found"):
        repo.apply("missing", "REAL", "SPEND", 1)
    repository()
    with pytest.raises(BudgetPersistenceError, match="actor"):
        repo.apply("budget", "REAL", "ALLOCATE", 1)


def test_repository_native_operations_and_restart():
    repo = repository()
    for operation, amount in [
        ("ALLOCATE", 5),
        ("RESERVE", 10),
        ("SETTLE", 4),
        ("RELEASE", 6),
        ("SPEND", 3),
        ("REFUND", 2),
        ("WIN", 1),
    ]:
        assert (
            repo.apply("budget", "REAL", operation, amount, actor="approved")["status"] == "APPLIED"
        )
    close_memory_clients()
    row = BudgetRepository().configure("budget", account(allocated="999"))
    assert row["allocated"] == "105.00"
    assert row["spent"] == "5.00"
    assert row["reserved"] == "0.00"
    assert row["won"] == "1.00"
    assert row["version"] == 7
    assert (
        BudgetRepository().configure("budget", account(AccountType.DEMO))["allocated"] == "100.00"
    )


def test_idempotency_is_durable_and_conflicts_fail():
    repo = repository()
    first = repo.apply("budget", "REAL", "SPEND", 5, idempotency_key="same")
    close_memory_clients()
    assert repo.apply("budget", "REAL", "SPEND", 5, idempotency_key="same") == first
    with pytest.raises(BudgetPersistenceError, match="conflicts"):
        repo.apply("budget", "DEMO", "SPEND", 5, idempotency_key="same")
    assert len(db.get_store().items(prefix="budget/operation/")) == 1


def test_concurrent_spending_cannot_exceed_authoritative_balance():
    repo = repository()

    def spend(_index):
        try:
            repo.apply("budget", "REAL", "SPEND", 60)
            return True
        except BudgetPersistenceError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(spend, range(2))) == 1
    assert repo.configure("budget", account())["spent"] == "60.00"


@pytest.mark.parametrize(
    "operation,amount",
    [("SETTLE", 1), ("RELEASE", 1), ("REFUND", 1), ("SPEND", 101), ("UNKNOWN", 0)],
)
def test_rejected_mutation_does_not_create_operation_or_change_balance(operation, amount):
    repo = repository()
    before = db.get_store().last_commit
    with pytest.raises(BudgetPersistenceError):
        repo.apply("budget", "REAL", operation, amount)
    assert db.get_store().last_commit == before
    assert db.get_store().items(prefix="budget/operation/") == {}


def test_repository_emits_execution_trace_event(monkeypatch):
    events = []

    class Trace:
        def add_event(self, event, payload):
            events.append((event, payload))

    monkeypatch.setattr("budget_repository.get_current_trace", lambda: Trace())
    repository().apply("budget", "DEMO", "WIN", 5, actor="provider")
    assert events[0][0] == "budget_operation_persisted"
    assert events[0][1]["account_type"] == "DEMO"
