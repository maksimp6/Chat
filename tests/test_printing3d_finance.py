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


def test_ai_assessment_accepts_sparse_terms_and_reports_only_missing_credit_data():
    from printing3d.finance import assess_financing_plan

    result = assess_financing_plan(
        {
            "purchase_mode": "credit",
            "equipment_price": 30000,
            "startup_costs": None,
            "financed_principal": None,
            "monthly_payment": None,
            "term_months": None,
            "planned_monthly_profit": None,
            "target_payment_coverage": None,
            "psk_percent": None,
            "currency": "RUB",
        },
        observed_monthly_profit=7200,
    )

    assert result["status"] == "needs_terms"
    assert result["missing_fields"] == [
        "financed_principal",
        "monthly_payment",
        "term_months",
    ]
    assert result["profit_source"] == "realized_3d_profit_last_30_days"
    assert result["known_inputs"]["planned_monthly_profit"] == 7200
    assert result["known_inputs"]["startup_costs"] == 0
    assert result["known_inputs"]["target_payment_coverage"] == 2
    assert result["plan"] is None


def test_ai_assessment_builds_ready_plan_from_collected_terms_and_observed_profit():
    from printing3d.finance import assess_financing_plan

    result = assess_financing_plan(
        {
            "purchase_mode": None,
            "equipment_price": 25000,
            "startup_costs": 5000,
            "financed_principal": 20000,
            "monthly_payment": 2500,
            "term_months": 10,
            "planned_monthly_profit": None,
            "target_payment_coverage": None,
            "psk_percent": None,
            "currency": None,
        },
        observed_monthly_profit=6000,
    )

    assert result["status"] == "ready"
    assert result["purchase_mode"] == "credit"
    assert result["currency"] == "RUB"
    assert result["missing_fields"] == []
    assert result["plan"]["total_business_outlay"] == 35000
    assert result["plan"]["required_monthly_profit"] == 5000
    assert result["plan"]["payment_coverage_ok"] is True
    assert result["plan"]["planned_surplus_after_payment"] == 3500


def test_ai_assessment_supports_cash_mode_without_credit_fields():
    from printing3d.finance import assess_financing_plan

    result = assess_financing_plan(
        {
            "purchase_mode": "cash",
            "equipment_price": 30000,
            "startup_costs": None,
            "financed_principal": None,
            "monthly_payment": None,
            "term_months": None,
            "planned_monthly_profit": 5000,
            "target_payment_coverage": None,
            "psk_percent": None,
            "currency": "RUB",
        }
    )

    assert result["status"] == "ready"
    assert result["plan"]["financed_principal"] == 0
    assert result["plan"]["monthly_payment"] == 0
    assert result["plan"]["payback_months"] == 6


def test_ai_assessment_validates_input_shape_and_purchase_mode():
    from printing3d.finance import assess_financing_plan

    with pytest.raises(ValueError, match="object"):
        assess_financing_plan([])
    with pytest.raises(ValueError, match="credit or cash"):
        assess_financing_plan({"purchase_mode": "lease"})


@pytest.fixture
def finance_db(tmp_path, monkeypatch):
    import db
    import printing3d.service as service

    path = tmp_path / "printing3d-finance.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setattr(service, "get_conn", db.get_conn)
    db.init_db()
    service.init_3d_printing_tables()
    return path


def test_recent_profit_is_owner_currency_and_window_scoped(finance_db):
    from printing3d import create_order, settle_order
    from printing3d.service import get_recent_profit

    first = create_order("owner-a", {"title": "RUB job", "quoted_price": 500})
    settle_order("owner-a", first["id"], actual_revenue=500, actual_cost=200)

    second = create_order(
        "owner-a",
        {"title": "USD job", "quoted_price": 100, "currency": "USD"},
    )
    settle_order("owner-a", second["id"], actual_revenue=100, actual_cost=25)

    other = create_order("owner-b", {"title": "Other owner", "quoted_price": 900})
    settle_order("owner-b", other["id"], actual_revenue=900, actual_cost=100)

    assert get_recent_profit("owner-a", currency="RUB") == pytest.approx(300)
    assert get_recent_profit("owner-a", currency="USD") == pytest.approx(75)
    with pytest.raises(ValueError, match="days must be positive"):
        get_recent_profit("owner-a", days=0)


def test_financing_tool_is_ai_first_read_only_and_uses_observed_profit(monkeypatch):
    from printing3d import tools as printing_tools
    from tool_registry import ToolRegistry

    monkeypatch.setattr(printing_tools, "get_recent_profit", lambda owner, currency, days: 6000)

    registry = ToolRegistry()
    meta = registry.get_tool_meta("printing3d.finance.assess")

    assert meta is not None
    assert meta["read_only"] is True
    assert meta["requires_approval"] is False
    assert {"3d", "treasury", "finance", "read"} <= set(meta["capabilities"])
    assert registry.get_tool_meta("printing3d.finance.calculate") is None

    class Call:
        user_id = "trusted-owner"

    result = registry.execute(
        "printing3d.finance.assess",
        {
            "purchase_mode": "credit",
            "equipment_price": 25000,
            "startup_costs": None,
            "financed_principal": 20000,
            "monthly_payment": 2500,
            "term_months": 10,
            "planned_monthly_profit": None,
            "target_payment_coverage": None,
            "psk_percent": None,
            "currency": "RUB",
        },
        context={"call": Call()},
    )

    assert result["status"] == "ready"
    assert result["observed_monthly_profit"] == 6000
    assert result["profit_source"] == "realized_3d_profit_last_30_days"
    assert result["plan"]["payment_coverage_ok"] is True
