"""RED safety contract for paid Cloud.ru owner-connect acceptance.

This is intentionally a tests-only RED slice. It must fail on protected master
until the runtime budget/deadline/cleanup guard exists. It does not create,
wake, scale, or otherwise mutate Cloud.ru resources.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from alice_platform.config import load_config


EXPECTED_IDLE_SECONDS = {
    "alice": 60,
    "alice-lab": 30,
    "alice-browser": 30,
}


def test_live_acceptance_runtime_windows_are_short_and_single_instance():
    config = load_config(Path("config/alice"))
    services = config["services"]

    for name, idle_seconds in EXPECTED_IDLE_SECONDS.items():
        service = services[name]
        assert service["min_instances"] == 0
        assert service["max_instances"] == 1
        assert service["idle_timeout_seconds"] == idle_seconds


def test_live_acceptance_budget_contract_is_fail_closed():
    from cloud.cloudru import live_budget

    policy = live_budget.LiveAcceptanceBudget(
        max_rub=Decimal("5"),
        max_window_seconds=15 * 60,
    )
    assert policy.max_rub == Decimal("5")
    assert policy.max_window_seconds == 900
    assert policy.action_on_limit == "deny"


def test_preflight_denies_predicted_cost_over_five_rubles():
    from cloud.cloudru import live_budget

    policy = live_budget.LiveAcceptanceBudget(
        max_rub=Decimal("5"),
        max_window_seconds=900,
    )
    with pytest.raises(live_budget.BudgetExceeded):
        policy.authorize(predicted_rub=Decimal("5.0000001"), elapsed_seconds=0)


def test_preflight_denies_window_at_or_after_fifteen_minutes():
    from cloud.cloudru import live_budget

    policy = live_budget.LiveAcceptanceBudget(
        max_rub=Decimal("5"),
        max_window_seconds=900,
    )
    with pytest.raises(live_budget.BudgetExceeded):
        policy.authorize(predicted_rub=Decimal("0"), elapsed_seconds=900)


def test_free_tier_never_increases_gross_run_budget():
    from cloud.cloudru import live_budget

    policy = live_budget.LiveAcceptanceBudget(
        max_rub=Decimal("5"),
        max_window_seconds=900,
    )
    with pytest.raises(live_budget.BudgetExceeded):
        policy.authorize(
            predicted_rub=Decimal("6"),
            elapsed_seconds=10,
            free_tier_remaining_rub=Decimal("100"),
        )


@pytest.mark.parametrize("outcome", ["success", "failure", "timeout", "cancelled", "exception"])
def test_every_terminal_outcome_requires_cleanup_to_zero(outcome):
    from cloud.cloudru import live_budget

    policy = live_budget.LiveAcceptanceBudget(
        max_rub=Decimal("5"),
        max_window_seconds=900,
    )
    decision = policy.finish(outcome)
    assert decision.cleanup_required is True
    assert decision.target_min_instances == 0
    assert decision.acceptance_passed is (outcome == "success")


def test_cleanup_failure_fails_acceptance():
    from cloud.cloudru import live_budget

    policy = live_budget.LiveAcceptanceBudget(
        max_rub=Decimal("5"),
        max_window_seconds=900,
    )
    result = policy.record_cleanup(success=False)
    assert result.acceptance_passed is False
    assert result.reason == "cleanup_failed"


def test_retry_requires_a_new_explicit_run_budget():
    from cloud.cloudru import live_budget

    policy = live_budget.LiveAcceptanceBudget(
        max_rub=Decimal("5"),
        max_window_seconds=900,
    )
    policy.finish("failure")
    with pytest.raises(live_budget.BudgetExceeded):
        policy.authorize(predicted_rub=Decimal("0.01"), elapsed_seconds=1)
