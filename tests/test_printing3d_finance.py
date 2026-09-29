import pytest


def test_financing_plan_calculates_credit_burden_and_payback():
    from printing3d.finance import calculate_financing_plan

    result = calculate_financing_plan(
        {
            "equipment_price": 30000,
            "startup_costs": 5000,
            "financed_principal": 27000,
            "monthly_payment": 3000,
            "term_months": 12,
            "planned_monthly_profit": 8000,
            "target_payment_coverage": 2,
            "psk_percent": 29.9,
            "currency": "RUB",
        }
    )

    assert result == {
        "currency": "RUB",
        "equipment_price": 30000.0,
        "startup_costs": 5000.0,
        "financed_principal": 27000.0,
        "credit_share_percent": 90.0,
        "monthly_payment": 3000.0,
        "term_months": 12,
        "psk_percent": 29.9,
        "total_credit_repayment": 36000.0,
        "financing_cost": 9000.0,
        "initial_cash_required": 8000.0,
        "total_business_outlay": 44000.0,
        "planned_monthly_profit": 8000.0,
        "target_payment_coverage": 2.0,
        "required_monthly_profit": 6000.0,
        "planned_surplus_after_payment": 5000.0,
        "payment_coverage_ratio": 2.67,
        "payment_coverage_ok": True,
        "payback_months": 5.5,
        "initial_cash_payback_months": 1.0,
    }


def test_financing_plan_supports_cash_purchase_and_zero_profit():
    from printing3d.finance import calculate_financing_plan

    result = calculate_financing_plan(
        {
            "equipment_price": 30000,
            "startup_costs": 4000,
            "financed_principal": 0,
            "monthly_payment": 0,
            "term_months": 0,
            "planned_monthly_profit": 0,
        }
    )

    assert result["currency"] == "RUB"
    assert result["psk_percent"] is None
    assert result["total_credit_repayment"] == 0
    assert result["financing_cost"] == 0
    assert result["initial_cash_required"] == 34000
    assert result["payment_coverage_ratio"] is None
    assert result["payment_coverage_ok"] is None
    assert result["payback_months"] is None
    assert result["initial_cash_payback_months"] is None


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "financing input must be an object"),
        ({"equipment_price": "nope"}, "equipment_price must be numeric"),
        ({"equipment_price": -1}, "equipment_price must be >= 0"),
        ({"equipment_price": 0}, "equipment_price must be > 0"),
        (
            {"equipment_price": 100, "financed_principal": 101},
            "financed_principal cannot exceed equipment_price",
        ),
        (
            {
                "equipment_price": 100,
                "financed_principal": 80,
                "monthly_payment": 0,
                "term_months": 12,
            },
            "credit requires monthly_payment and term_months",
        ),
        (
            {
                "equipment_price": 100,
                "financed_principal": 80,
                "monthly_payment": 5,
                "term_months": 12,
            },
            "total credit repayment cannot be below financed principal",
        ),
        (
            {
                "equipment_price": 100,
                "monthly_payment": 1,
                "term_months": 0,
            },
            "cash purchase cannot have credit payment or term",
        ),
        (
            {
                "equipment_price": 100,
                "term_months": 1.5,
            },
            "term_months must be a whole number",
        ),
        (
            {
                "equipment_price": 100,
                "term_months": "months",
            },
            "term_months must be numeric",
        ),
        (
            {
                "equipment_price": 100,
                "target_payment_coverage": 0.5,
            },
            "target_payment_coverage must be >= 1",
        ),
    ],
)
def test_financing_plan_rejects_invalid_terms(payload, message):
    from printing3d.finance import calculate_financing_plan

    with pytest.raises(ValueError, match=message):
        calculate_financing_plan(payload)


def test_financing_tool_is_registered_read_only_and_executes():
    from tool_registry import ToolRegistry

    registry = ToolRegistry()
    meta = registry.get_tool_meta("printing3d.finance.calculate")

    assert meta is not None
    assert meta["read_only"] is True
    assert meta["requires_approval"] is False
    assert {"3d", "treasury", "finance", "read"} <= set(meta["capabilities"])

    result = registry.execute(
        "printing3d.finance.calculate",
        {
            "equipment_price": 25000,
            "startup_costs": 5000,
            "financed_principal": 20000,
            "monthly_payment": 2500,
            "term_months": 10,
            "planned_monthly_profit": 6000,
            "target_payment_coverage": None,
            "psk_percent": None,
            "currency": "RUB",
        },
    )

    assert result["total_business_outlay"] == 35000
    assert result["target_payment_coverage"] == 2
    assert result["required_monthly_profit"] == 5000
    assert result["payment_coverage_ok"] is True
    assert result["planned_surplus_after_payment"] == 3500
