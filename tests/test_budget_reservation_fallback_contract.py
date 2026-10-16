"""RED contract for pending budget exposure and REAL/DEMO fallback (#1181).

The first four tests are expected to fail against the 2026-10-12 master.
The rest protect already documented policy-controlled fallback behavior.
"""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from economy.budget_controller import (
    AccountType,
    BudgetAccount,
    BudgetController,
    BudgetLimitExceeded,
    BudgetLimits,
    InsufficientFunds,
)


def _controller(
    *,
    real="100",
    demo="100",
    daily="60",
    total="300",
    single="50",
    fallback=False,
    events=None,
):
    limits = BudgetLimits(single, daily, total)
    return BudgetController(
        "RESERVATION-CONTRACT-1181",
        BudgetAccount(AccountType.REAL, "EUR", allocated=real, limits=limits),
        BudgetAccount(AccountType.DEMO, "EUR", allocated=demo, limits=limits),
        trace_sink=events.append if events is not None else None,
        fallback_to_demo=fallback,
    )


def test_pending_reservations_count_against_daily_loss_admission():
    events = []
    controller = _controller(daily="60", total="300", events=events)
    controller.reserve("40", account_type=AccountType.REAL)

    with pytest.raises(BudgetLimitExceeded):
        controller.reserve("30", account_type=AccountType.REAL)

    real = controller.account(AccountType.REAL)
    assert real.reserved == Decimal("40.00")
    assert real.spent == Decimal("0.00")
    assert [event["event"] for event in events].count("reservation") == 1


def test_existing_reservation_counts_toward_total_loss_before_direct_spend():
    controller = _controller(daily="300", total="60")
    controller.reserve("40", account_type=AccountType.REAL)

    with pytest.raises(BudgetLimitExceeded):
        controller.spend("30", account_type=AccountType.REAL)

    real = controller.account(AccountType.REAL)
    assert real.reserved == Decimal("40.00")
    assert real.spent == Decimal("0.00")


def test_concurrent_reservations_cannot_overcommit_loss_exposure():
    controller = _controller(real="1000", daily="70", total="70")

    def try_reserve(_):
        try:
            controller.reserve("20", account_type=AccountType.REAL)
            return True
        except BudgetLimitExceeded:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        admitted = list(pool.map(try_reserve, range(12)))

    assert sum(admitted) == 3
    real = controller.account(AccountType.REAL)
    assert real.reserved == Decimal("60.00")
    assert real.spent == Decimal("0.00")


def test_release_restores_exposure_headroom():
    controller = _controller(daily="60")
    controller.reserve("50", account_type=AccountType.REAL)
    with pytest.raises(BudgetLimitExceeded):
        controller.reserve("20", account_type=AccountType.REAL)
    controller.release_reservation("50", account_type=AccountType.REAL)
    controller.reserve("20", account_type=AccountType.REAL)
    assert controller.account(AccountType.REAL).reserved == Decimal("20.00")


def test_exhausted_real_reservation_falls_back_to_demo_only_when_enabled():
    events = []
    controller = _controller(real="0", demo="40", fallback=True, events=events)
    controller.reserve("10")

    assert controller.active_account is AccountType.DEMO
    assert controller.account(AccountType.REAL).reserved == Decimal("0.00")
    assert controller.account(AccountType.REAL).spent == Decimal("0.00")
    assert controller.account(AccountType.DEMO).reserved == Decimal("10.00")
    assert any(
        event["event"] == "mode_switch" and event.get("reason") == "real_budget_exhausted"
        for event in events
    )


def test_disabled_fallback_denies_real_reservation_without_touching_demo():
    controller = _controller(real="0", demo="40", fallback=False)
    with pytest.raises(InsufficientFunds):
        controller.reserve("10")

    assert controller.active_account is AccountType.REAL
    assert controller.account(AccountType.REAL).reserved == Decimal("0.00")
    assert controller.account(AccountType.DEMO).reserved == Decimal("0.00")


def test_unavailable_demo_fallback_never_creates_phantom_reservation():
    events = []
    controller = _controller(real="0", demo="0", fallback=True, events=events)
    with pytest.raises(InsufficientFunds):
        controller.reserve("10")

    for kind in (AccountType.REAL, AccountType.DEMO):
        account = controller.account(kind)
        assert account.reserved == Decimal("0.00")
        assert account.spent == Decimal("0.00")
    assert not any(event["event"] in {"reservation", "spend"} for event in events)


def test_operation_limit_denial_does_not_trigger_demo_fallback():
    controller = _controller(real="0", demo="100", single="5", fallback=True)
    with pytest.raises(BudgetLimitExceeded):
        controller.reserve("10")

    assert controller.active_account is AccountType.REAL
    assert controller.account(AccountType.DEMO).reserved == Decimal("0.00")
