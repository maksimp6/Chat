from decimal import Decimal

import pytest

from budget_controller import (
    AccountType,
    BudgetAccount,
    BudgetController,
    BudgetLimits,
    BudgetLimitExceeded,
    DemoConversionDenied,
    InsufficientFunds,
)


def make_controller(trace=None, *, fallback=True):
    limits = BudgetLimits("50", "100", "300")
    return BudgetController(
        "GAMBLING-001",
        BudgetAccount(AccountType.REAL, "EUR", allocated="100", limits=limits),
        BudgetAccount(AccountType.DEMO, "EUR", allocated="1000", limits=limits),
        trace_sink=trace,
        fallback_to_demo=fallback,
    )


def test_real_and_demo_are_separate():
    controller = make_controller()
    controller.spend("25", account_type=AccountType.REAL)
    controller.spend("25", account_type=AccountType.DEMO)

    real = controller.account(AccountType.REAL)
    demo = controller.account(AccountType.DEMO)
    assert real.spent == Decimal("25.00")
    assert demo.spent == Decimal("25.00")
    assert real.available == Decimal("75.00")
    assert demo.available == Decimal("975.00")


def test_real_exhaustion_switches_to_demo():
    controller = make_controller()
    controller.spend("50", account_type=AccountType.REAL)
    controller.spend("50", account_type=AccountType.REAL)

    assert controller.active_account is AccountType.REAL
    controller.spend("1")
    assert controller.active_account is AccountType.DEMO
    assert controller.account(AccountType.REAL).available == Decimal("0.00")
    assert controller.account(AccountType.DEMO).spent == Decimal("1.00")


def test_demo_cannot_be_converted_to_real():
    controller = make_controller()
    with pytest.raises(DemoConversionDenied):
        controller.convert_demo_to_real("10")


def test_limits_are_deterministic():
    controller = make_controller()
    with pytest.raises(BudgetLimitExceeded):
        controller.spend("51", account_type=AccountType.REAL)


def test_insufficient_budget_without_fallback():
    controller = make_controller(fallback=False)
    controller.spend("50", account_type=AccountType.REAL)
    controller.spend("50", account_type=AccountType.REAL)
    with pytest.raises(InsufficientFunds):
        controller.spend("1", account_type=AccountType.REAL)


def test_replenishment_requires_policy_approval():
    controller = make_controller()
    with pytest.raises(Exception):
        controller.request_replenishment(
            AccountType.REAL, "10", requested_by="agent", policy_check=lambda *_: False
        )

    controller.request_replenishment(
        AccountType.REAL, "10", requested_by="agent", policy_check=lambda *_: True
    )
    assert controller.account(AccountType.REAL).allocated == Decimal("110.00")


def test_trace_events_never_contain_secret_like_fields():
    events = []
    controller = make_controller(events.append)
    controller.spend("10")
    assert events
    for event in events:
        assert not any("secret" in key.lower() or "token" in key.lower() for key in event)


def test_snapshot_contains_required_budget_fields():
    snapshot = make_controller().snapshot()
    real = snapshot["accounts"]["REAL"]
    assert set(real) >= {
        "allocated",
        "spent",
        "reserved",
        "available",
        "currency",
        "account_type",
        "status",
        "limits",
    }
