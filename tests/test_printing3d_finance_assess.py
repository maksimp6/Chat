import pytest


@pytest.fixture
def assess_db(tmp_path, monkeypatch):
    import db
    import printing3d.service as service

    path = tmp_path / "printing3d-finance-assess.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setattr(service, "get_conn", db.get_conn)
    db.init_db()
    service.init_3d_printing_tables()
    return path


def _candidate(**overrides):
    data = {
        "purchase_mode": "credit",
        "printer_model": "Bambu Lab A1",
        "equipment_price": 30000,
        "setup_cost": 5000,
        "down_payment": 5000,
        "credit_principal": 25000,
        "credit_total_repayment": 31000,
        "monthly_payment": 3100,
        "term_months": 10,
        "psk_percent": 24.5,
        "currency": "RUB",
        "expected_monthly_profit": 8000,
        "target_payment_coverage": 2,
    }
    data.update(overrides)
    return data


def test_assess_can_start_without_manual_finance_input(assess_db):
    from printing3d.finance import assess_financing

    result = assess_financing("owner-a", {})

    assert result["status"] == "needs_terms"
    assert result["purchase_mode"] == "credit"
    assert result["missing_fields"] == [
        "equipment_price",
        "credit_principal",
        "monthly_payment",
        "term_months",
    ]
    assert result["resolved_terms"]["setup_cost"] == 0
    assert result["resolved_terms"]["down_payment"] is None
    assert result["resolved_terms"]["currency"] == "RUB"
    assert result["saved_plan_used"] is False
    assert result["profit_source"] == "none"
    assert result["profit_basis"] is None
    assert result["analysis"] is None
    assert "setup_cost=0" in result["assumptions"]
    assert "down_payment=derived_from_purchase_terms" in result["assumptions"]
    assert "target_payment_coverage=2" in result["assumptions"]


def test_assess_uses_saved_credit_plan_and_realized_profit(assess_db):
    from printing3d import create_order, settle_order
    from printing3d.finance import assess_financing, set_financing_plan

    set_financing_plan(
        "owner-a",
        {
            "printer_model": "Bambu Lab A1",
            "equipment_price": 30000,
            "setup_cost": 5000,
            "down_payment": 5000,
            "credit_principal": 25000,
            "credit_total_repayment": 31000,
            "monthly_payment": 3100,
            "term_months": 10,
            "psk_percent": 24.5,
            "currency": "RUB",
        },
    )
    order = create_order("owner-a", {"title": "Production", "quoted_price": 10000})
    settle_order("owner-a", order["id"], actual_revenue=10000, actual_cost=2000)

    result = assess_financing("owner-a", {})

    assert result["status"] == "ready"
    assert result["saved_plan_used"] is True
    assert result["resolved_terms"]["printer_model"] == "Bambu Lab A1"
    assert result["resolved_terms"]["psk_percent"] == pytest.approx(24.5)
    assert result["profit_source"] == "realized_current_month"
    assert result["profit_basis"] == pytest.approx(8000)
    assert result["observed_monthly_profit"] == pytest.approx(8000)
    assert result["cumulative_realized_profit"] == pytest.approx(8000)
    assert result["analysis"]["known_cost_basis"] == pytest.approx(41000)
    assert result["analysis"]["upfront_cash_required"] == pytest.approx(10000)
    assert result["analysis"]["total_credit_repayment"] == pytest.approx(31000)
    assert result["analysis"]["financing_cost"] == pytest.approx(6000)
    assert result["analysis"]["required_monthly_profit"] == pytest.approx(6200)
    assert result["analysis"]["payment_coverage_ratio"] == pytest.approx(2.58)
    assert result["analysis"]["payment_coverage_ok"] is True
    assert result["analysis"]["estimated_payback_months"] == pytest.approx(5.13)
    assert result["analysis"]["profit_evidence_status"] == "available"


def test_assess_derives_down_payment_and_total_repayment_from_ai_terms(assess_db):
    from printing3d.finance import assess_financing

    result = assess_financing(
        "owner-a",
        _candidate(
            down_payment=None,
            credit_total_repayment=None,
            psk_percent=None,
        ),
    )

    assert result["status"] == "ready"
    assert result["saved_plan_used"] is False
    assert result["profit_source"] == "provided_or_agent_estimate"
    assert result["resolved_terms"]["down_payment"] == pytest.approx(5000)
    assert result["analysis"]["total_credit_repayment"] == pytest.approx(31000)
    assert result["analysis"]["financing_cost"] == pytest.approx(6000)
    assert result["analysis"]["upfront_cash_required"] == pytest.approx(10000)
    assert result["analysis"]["payment_coverage_ok"] is True


def test_assess_cash_purchase_uses_full_equipment_price_upfront(assess_db):
    from printing3d.finance import assess_financing

    result = assess_financing(
        "owner-a",
        {
            "purchase_mode": "cash",
            "equipment_price": 30000,
            "setup_cost": 5000,
            "expected_monthly_profit": 7000,
        },
    )

    assert result["status"] == "ready"
    assert result["resolved_terms"]["credit_principal"] == 0
    assert result["resolved_terms"]["monthly_payment"] == 0
    assert result["resolved_terms"]["term_months"] is None
    assert result["resolved_terms"]["down_payment"] == pytest.approx(30000)
    assert result["analysis"]["known_cost_basis"] == pytest.approx(35000)
    assert result["analysis"]["upfront_cash_required"] == pytest.approx(35000)
    assert result["analysis"]["required_monthly_profit"] == 0
    assert result["analysis"]["payment_coverage_ratio"] is None
    assert result["analysis"]["payment_coverage_ok"] is None
    assert result["analysis"]["estimated_payback_months"] == pytest.approx(5)


def test_assess_infers_cash_from_saved_cash_plan(assess_db):
    from printing3d.finance import assess_financing, set_financing_plan

    set_financing_plan(
        "owner-a",
        {
            "printer_model": "Existing printer",
            "equipment_price": 20000,
            "setup_cost": 1000,
            "down_payment": 20000,
            "credit_principal": 0,
            "currency": "RUB",
        },
    )

    result = assess_financing(
        "owner-a",
        {"expected_monthly_profit": 5000},
    )

    assert result["purchase_mode"] == "cash"
    assert result["status"] == "ready"
    assert result["analysis"]["known_cost_basis"] == pytest.approx(21000)
    assert result["analysis"]["upfront_cash_required"] == pytest.approx(21000)


@pytest.mark.parametrize(
    ("owner_id", "payload", "message"),
    [
        ("", {}, "owner identity is required"),
        ("owner-a", [], "finance assessment must be an object"),
        ("owner-a", {"purchase_mode": "lease"}, "purchase_mode must be credit or cash"),
        (
            "owner-a",
            _candidate(target_payment_coverage=0.5),
            "target_payment_coverage must be >= 1",
        ),
        (
            "owner-a",
            _candidate(monthly_payment=0),
            "monthly_payment must be > 0",
        ),
        (
            "owner-a",
            _candidate(equipment_price=20000, credit_principal=25000),
            "credit_principal cannot exceed equipment_price",
        ),
        (
            "owner-a",
            _candidate(credit_total_repayment=24000),
            "credit_total_repayment must be >= credit_principal",
        ),
        (
            "owner-a",
            _candidate(psk_percent=-1),
            "psk_percent must be >= 0",
        ),
    ],
)
def test_assess_rejects_invalid_candidate_terms(assess_db, owner_id, payload, message):
    from printing3d.finance import assess_financing

    with pytest.raises(ValueError, match=message):
        assess_financing(owner_id, payload)


def test_assess_can_be_ready_without_profit_evidence(assess_db):
    from printing3d.finance import assess_financing

    result = assess_financing(
        "owner-a",
        _candidate(expected_monthly_profit=None),
    )

    assert result["status"] == "ready"
    assert result["profit_source"] == "none"
    assert result["profit_basis"] is None
    assert result["analysis"]["profit_evidence_status"] == "not_available"
    assert result["analysis"]["payment_coverage_ratio"] is None
    assert result["analysis"]["payment_coverage_ok"] is None
    assert result["analysis"]["estimated_payback_months"] is None


def test_finance_assess_tool_is_read_only_and_uses_trusted_identity(assess_db, monkeypatch):
    import printing3d.tools as printing_tools
    from tool_registry import ToolRegistry

    captured = {}

    def fake_assess(owner_id, data):
        captured["owner_id"] = owner_id
        captured["data"] = data
        return {"status": "ready"}

    monkeypatch.setattr(printing_tools, "assess_financing", fake_assess)

    class Call:
        user_id = "trusted-owner"

    registry = ToolRegistry()
    meta = registry.get_tool_meta("printing3d.finance.assess")
    result = registry.execute(
        "printing3d.finance.assess",
        {
            "purchase_mode": None,
            "printer_model": None,
            "equipment_price": None,
            "setup_cost": None,
            "down_payment": None,
            "credit_principal": None,
            "credit_total_repayment": None,
            "monthly_payment": None,
            "term_months": None,
            "psk_percent": None,
            "currency": None,
            "expected_monthly_profit": None,
            "target_payment_coverage": None,
        },
        context={"call": Call()},
    )

    assert result == {"status": "ready"}
    assert captured["owner_id"] == "trusted-owner"
    assert captured["data"]["equipment_price"] is None
    assert meta["read_only"] is True
    assert meta["requires_approval"] is False
    assert {"3d", "treasury", "finance", "read"} <= set(meta["capabilities"])
