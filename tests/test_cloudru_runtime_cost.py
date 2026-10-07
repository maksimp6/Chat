from decimal import Decimal

import pytest

from cloud.cloudru.billing import estimate_container_runtime_cost


def test_estimate_container_runtime_cost_for_alice():
    result = estimate_container_runtime_cost(
        active_seconds=3600,
        idle_seconds=300,
        cold_starts=1,
        vcpu=Decimal("1"),
        memory_gb=Decimal("1"),
        vcpu_rub_per_hour=Decimal("1.891"),
        memory_rub_per_gb_hour=Decimal("1.256966"),
    )

    assert result["billable_seconds"] == 3900
    assert result["active_seconds"] == 3600
    assert result["idle_seconds"] == 300
    assert result["cold_starts"] == 1
    assert result["estimated_rub"] == Decimal("3.4102965")
    assert result["status"] == "estimated"


def test_zero_usage_costs_zero():
    result = estimate_container_runtime_cost(
        active_seconds=0,
        idle_seconds=0,
        cold_starts=0,
        vcpu=Decimal("2"),
        memory_gb=Decimal("2"),
        vcpu_rub_per_hour=Decimal("1.891"),
        memory_rub_per_gb_hour=Decimal("1.256966"),
    )
    assert result["billable_seconds"] == 0
    assert result["estimated_rub"] == Decimal("0")
    assert result["status"] == "estimated"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("active_seconds", -1),
        ("idle_seconds", -1),
        ("cold_starts", -1),
        ("vcpu", Decimal("-1")),
        ("memory_gb", Decimal("-1")),
        ("vcpu_rub_per_hour", Decimal("-1")),
        ("memory_rub_per_gb_hour", Decimal("-1")),
    ],
)
def test_negative_runtime_inputs_rejected(field, value):
    kwargs = {
        "active_seconds": 10,
        "idle_seconds": 2,
        "cold_starts": 1,
        "vcpu": Decimal("1"),
        "memory_gb": Decimal("1"),
        "vcpu_rub_per_hour": Decimal("1.891"),
        "memory_rub_per_gb_hour": Decimal("1.256966"),
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match=field):
        estimate_container_runtime_cost(**kwargs)


def test_runtime_estimate_does_not_apply_free_tier_implicitly():
    result = estimate_container_runtime_cost(
        active_seconds=3600,
        idle_seconds=0,
        cold_starts=1,
        vcpu=Decimal("1"),
        memory_gb=Decimal("1"),
        vcpu_rub_per_hour=Decimal("1.891"),
        memory_rub_per_gb_hour=Decimal("1.256966"),
    )
    assert result["estimated_rub"] == Decimal("3.147966")
    assert result["free_tier_applied"] is False


def test_short_idle_is_cheaper_than_default_idle_for_same_active_work():
    common = {
        "active_seconds": 60,
        "cold_starts": 1,
        "vcpu": Decimal("2"),
        "memory_gb": Decimal("2"),
        "vcpu_rub_per_hour": Decimal("1.891"),
        "memory_rub_per_gb_hour": Decimal("1.256966"),
    }
    short = estimate_container_runtime_cost(idle_seconds=120, **common)
    default = estimate_container_runtime_cost(idle_seconds=1800, **common)

    assert short["estimated_rub"] < default["estimated_rub"]
    assert default["estimated_rub"] - short["estimated_rub"] == Decimal("2.9381016")


def test_estimate_exposes_cost_components():
    result = estimate_container_runtime_cost(
        active_seconds=1800,
        idle_seconds=0,
        cold_starts=2,
        vcpu=Decimal("1"),
        memory_gb=Decimal("1"),
        vcpu_rub_per_hour=Decimal("1.891"),
        memory_rub_per_gb_hour=Decimal("1.256966"),
    )

    assert result["cpu_rub"] == Decimal("0.9455")
    assert result["memory_rub"] == Decimal("0.628483")
    assert result["estimated_rub"] == result["cpu_rub"] + result["memory_rub"]
