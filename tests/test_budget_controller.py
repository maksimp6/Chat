from datetime import datetime, timedelta, timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

import pytest

from budget_controller import (
    AccountType,
    AuthorizationRequired,
    BudgetAccount,
    BudgetController,
    BudgetLimitExceeded,
    BudgetLimits,
    DemoConversionDenied,
    InsufficientFunds,
)


def make_controller(trace=None, *, fallback=True, cooldown_seconds=0, now=None):
    limits = BudgetLimits("50", "100", "300")
    clock = (lambda: now) if now is not None else None
    return BudgetController(
        "GAMBLING-001",
        BudgetAccount(AccountType.REAL, "EUR", allocated="100", limits=limits),
        BudgetAccount(AccountType.DEMO, "EUR", allocated="1000", limits=limits),
        trace_sink=trace,
        fallback_to_demo=fallback,
        cooldown_seconds=cooldown_seconds,
        clock=clock,
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
    controller.spend("1")
    assert controller.active_account is AccountType.DEMO
    assert controller.account(AccountType.REAL).available == Decimal("0.00")
    assert controller.account(AccountType.DEMO).spent == Decimal("1.00")


def test_demo_cannot_be_converted_to_real():
    with pytest.raises(DemoConversionDenied):
        make_controller().convert_demo_to_real("10")


def test_demo_to_real_requires_explicit_external_authorization():
    controller = make_controller()
    controller.switch_to_demo(reason="test")
    with pytest.raises(AuthorizationRequired):
        controller.activate_real(actor="agent", authorization_check=lambda: False)
    controller.activate_real(actor="human", authorization_check=lambda: True)
    assert controller.active_account is AccountType.REAL


def test_limits_are_deterministic():
    with pytest.raises(BudgetLimitExceeded):
        make_controller().spend("51", account_type=AccountType.REAL)


def test_insufficient_budget_without_fallback():
    controller = make_controller(fallback=False)
    controller.spend("50", account_type=AccountType.REAL)
    controller.spend("50", account_type=AccountType.REAL)
    with pytest.raises(InsufficientFunds):
        controller.spend("1", account_type=AccountType.REAL)


def test_replenishment_requires_policy_approval():
    controller = make_controller()
    with pytest.raises(AuthorizationRequired):
        controller.request_replenishment(
            AccountType.REAL, "10", requested_by="agent", policy_check=lambda *_: False
        )
    controller.request_replenishment(
        AccountType.REAL, "10", requested_by="agent", policy_check=lambda *_: True
    )
    assert controller.account(AccountType.REAL).allocated == Decimal("110.00")


def test_win_increases_available():
    controller = make_controller()
    controller.record_win("150", account_type=AccountType.REAL)
    assert controller.account(AccountType.REAL).won == Decimal("150.00")
    assert controller.account(AccountType.REAL).available == Decimal("250.00")


def test_reservation_can_be_settled_or_released():
    controller = make_controller()
    controller.reserve("20", account_type=AccountType.REAL)
    controller.settle_reservation("10", account_type=AccountType.REAL)
    assert controller.account(AccountType.REAL).reserved == Decimal("10.00")
    assert controller.account(AccountType.REAL).spent == Decimal("10.00")
    controller.release_reservation("10", account_type=AccountType.REAL)
    assert controller.account(AccountType.REAL).reserved == Decimal("0.00")


def test_refund_reverses_loss():
    controller = make_controller()
    controller.spend("20", account_type=AccountType.REAL)
    controller.refund("5", account_type=AccountType.REAL)
    account = controller.account(AccountType.REAL)
    assert account.spent == Decimal("15.00")
    assert account.loss_today == Decimal("15.00")
    assert account.available == Decimal("85.00")


def test_daily_limit_starts_cooldown():
    now = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
    controller = make_controller(cooldown_seconds=3600, now=now)
    controller.spend("50", account_type=AccountType.REAL)
    controller.spend("50", account_type=AccountType.REAL)
    assert controller.account(AccountType.REAL).locked_until == now + timedelta(hours=1)
    with pytest.raises(BudgetLimitExceeded):
        controller.spend("1", account_type=AccountType.REAL)


def test_daily_loss_resets_on_new_day():
    now = datetime(2026, 9, 25, 23, 59, tzinfo=timezone.utc)
    controller = make_controller(now=now)
    controller.spend("50", account_type=AccountType.REAL)
    controller._clock = lambda: now + timedelta(minutes=2)
    controller.spend("40", account_type=AccountType.REAL)
    assert controller.account(AccountType.REAL).loss_today == Decimal("40.00")


def test_concurrent_spends_do_not_overspend():
    controller = make_controller(fallback=False)
    def spend():
        try:
            controller.spend("10", account_type=AccountType.REAL)
            return True
        except (InsufficientFunds, BudgetLimitExceeded):
            return False
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: spend(), range(16)))
    assert sum(results) == 10
    assert controller.account(AccountType.REAL).spent == Decimal("100.00")


def test_trace_events_never_contain_secret_like_fields():
    events = []
    make_controller(events.append).spend("10")
    for event in events:
        assert not any("secret" in key.lower() or "token" in key.lower() for key in event)


def test_snapshot_contains_required_budget_fields():
    real = make_controller().snapshot()["accounts"]["REAL"]
    assert set(real) >= {
        "allocated", "spent", "reserved", "won", "available",
        "currency", "account_type", "status", "limits", "locked_until",
    }
