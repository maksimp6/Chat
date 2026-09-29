import pytest


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


def _full_plan(**overrides):
    plan = {
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
        "started_at": "2026-09-29",
    }
    plan.update(overrides)
    return plan


def test_finance_plan_persists_updates_and_scopes_owner(finance_db):
    from printing3d.finance import get_financing_plan, set_financing_plan

    created = set_financing_plan("owner-a", _full_plan())
    assert created["printer_model"] == "Bambu Lab A1"
    assert created["equipment_price"] == pytest.approx(30000)
    assert created["credit_total_repayment"] == pytest.approx(31000)
    assert created["monthly_payment"] == pytest.approx(3100)
    assert created["term_months"] == 10
    assert created["psk_percent"] == pytest.approx(24.5)
    assert created["currency"] == "RUB"
    assert get_financing_plan("owner-b") is None

    updated = set_financing_plan(
        "owner-a",
        _full_plan(printer_model="Bambu Lab A1 Combo", equipment_price=45000),
    )
    assert updated["printer_model"] == "Bambu Lab A1 Combo"
    assert updated["equipment_price"] == pytest.approx(45000)


def test_finance_plan_validation(finance_db):
    from printing3d.finance import set_financing_plan

    with pytest.raises(ValueError, match="owner identity"):
        set_financing_plan("", _full_plan())
    with pytest.raises(ValueError, match="object"):
        set_financing_plan("owner-a", [])
    with pytest.raises(ValueError, match="printer_model"):
        set_financing_plan("owner-a", _full_plan(printer_model=""))
    with pytest.raises(ValueError, match="equipment_price must be numeric"):
        set_financing_plan("owner-a", _full_plan(equipment_price=None))
    with pytest.raises(ValueError, match="setup_cost must be >= 0"):
        set_financing_plan("owner-a", _full_plan(setup_cost=-1))
    with pytest.raises(ValueError, match="credit_total_repayment must be >="):
        set_financing_plan(
            "owner-a",
            _full_plan(credit_principal=30000, credit_total_repayment=29999),
        )
    with pytest.raises(ValueError, match="term_months is required"):
        set_financing_plan("owner-a", _full_plan(term_months=None))
    with pytest.raises(ValueError, match="monthly_payment is required"):
        set_financing_plan("owner-a", _full_plan(monthly_payment=None))
    with pytest.raises(ValueError, match="term_months must be an integer"):
        set_financing_plan("owner-a", _full_plan(term_months=True))
    with pytest.raises(ValueError, match="term_months must be an integer"):
        set_financing_plan("owner-a", _full_plan(term_months="10.5"))
    with pytest.raises(ValueError, match="term_months must be an integer"):
        set_financing_plan("owner-a", _full_plan(term_months=10.0))
    with pytest.raises(ValueError, match="term_months must be >= 1"):
        set_financing_plan("owner-a", _full_plan(term_months=0))


def test_complete_payback_status_uses_realized_profit(finance_db):
    from printing3d import create_order, settle_order
    from printing3d.finance import get_payback_status, set_financing_plan

    set_financing_plan("owner-a", _full_plan())
    first = create_order("owner-a", {"title": "Part 1", "quoted_price": 7000})
    second = create_order("owner-a", {"title": "Part 2", "quoted_price": 5000})
    settle_order("owner-a", first["id"], actual_revenue=7000, actual_cost=2000)
    settle_order("owner-a", second["id"], actual_revenue=5000, actual_cost=2000)

    status = get_payback_status("owner-a")

    assert status["status"] == "configured"
    assert status["cost_basis_status"] == "complete"
    assert status["known_cost_basis"] == pytest.approx(41000)
    assert status["credit_overpayment"] == pytest.approx(6000)
    assert status["cumulative_realized_profit"] == pytest.approx(8000)
    assert status["month_realized_profit"] == pytest.approx(8000)
    assert status["remaining_to_known_cost_basis"] == pytest.approx(33000)
    assert status["payback_progress_percent"] == pytest.approx(19.51)
    assert status["monthly_payment_coverage_ratio"] == pytest.approx(2.58)
    assert status["monthly_payment_covered"] is True
    assert status["break_even_reached"] is False


def test_payback_status_is_partial_when_credit_total_is_unknown(finance_db):
    from printing3d.finance import get_payback_status, set_financing_plan

    set_financing_plan(
        "owner-a",
        _full_plan(
            credit_total_repayment=None,
            monthly_payment=None,
            term_months=None,
            psk_percent=None,
        ),
    )

    status = get_payback_status("owner-a", month="2026-09")

    assert status["cost_basis_status"] == "partial"
    assert status["credit_overpayment"] is None
    assert status["known_cost_basis"] == pytest.approx(35000)
    assert status["monthly_payment_coverage_ratio"] is None
    assert status["monthly_payment_covered"] is None
    assert status["break_even_reached"] is None


def test_payback_status_handles_no_plan_zero_basis_and_invalid_month(finance_db):
    from printing3d.finance import get_payback_status, set_financing_plan

    assert get_payback_status("missing-owner") == {
        "plan": None,
        "status": "not_configured",
    }

    set_financing_plan(
        "owner-a",
        {
            "printer_model": "Existing printer",
            "equipment_price": 0,
            "setup_cost": 0,
            "credit_principal": 0,
            "currency": "RUB",
        },
    )
    status = get_payback_status("owner-a", month="2026-09")
    assert status["known_cost_basis"] == 0
    assert status["payback_progress_percent"] is None
    assert status["break_even_reached"] is True

    with pytest.raises(ValueError, match="YYYY-MM"):
        get_payback_status("owner-a", month="2026-13")


def test_month_profit_filters_currency_and_month(finance_db):
    import printing3d.service as service
    from printing3d import create_order, settle_order
    from printing3d.finance import get_payback_status, set_financing_plan

    set_financing_plan("owner-a", _full_plan())

    current = create_order("owner-a", {"title": "Current", "quoted_price": 1000})
    old = create_order("owner-a", {"title": "Old", "quoted_price": 1000})
    usd = create_order(
        "owner-a",
        {"title": "USD", "quoted_price": 1000, "currency": "USD"},
    )
    settle_order("owner-a", current["id"], actual_revenue=1000, actual_cost=200)
    settle_order("owner-a", old["id"], actual_revenue=1000, actual_cost=100)
    settle_order("owner-a", usd["id"], actual_revenue=1000, actual_cost=50)

    conn = service.get_conn()
    conn.execute(
        "UPDATE print_orders SET settled_at = ? WHERE id = ?",
        ("2020-01-05T00:00:00", old["id"]),
    )
    conn.commit()
    conn.close()

    current_month = get_payback_status("owner-a")["month"]
    status = get_payback_status("owner-a", month=current_month)

    assert status["cumulative_realized_profit"] == pytest.approx(1700)
    assert status["month_realized_profit"] == pytest.approx(800)


@pytest.fixture
def finance_client(finance_db, monkeypatch):
    monkeypatch.setenv("ALICE_OWNER_ID", "finance-route-owner")
    from app import app

    app.config.update(TESTING=True)
    return app.test_client()


def test_finance_routes_cover_plan_status_and_errors(finance_client):
    invalid_body = finance_client.put("/api/3d/finance/plan", json=[])
    assert invalid_body.status_code == 400

    invalid_plan = finance_client.put(
        "/api/3d/finance/plan",
        json={"printer_model": "A1"},
    )
    assert invalid_plan.status_code == 400
    assert invalid_plan.get_json()["error"] == "invalid finance plan"

    created = finance_client.put("/api/3d/finance/plan", json=_full_plan())
    assert created.status_code == 200
    assert created.get_json()["printer_model"] == "Bambu Lab A1"

    status = finance_client.get("/api/3d/finance/status?month=2026-09")
    assert status.status_code == 200
    assert status.get_json()["cost_basis_status"] == "complete"

    invalid_month = finance_client.get("/api/3d/finance/status?month=bad")
    assert invalid_month.status_code == 400
    assert invalid_month.get_json()["error"] == "invalid finance status request"


def test_finance_routes_require_owner(finance_db, monkeypatch):
    monkeypatch.delenv("ALICE_OWNER_ID", raising=False)
    from app import app

    app.config.update(TESTING=True)
    client = app.test_client()

    plan = client.put("/api/3d/finance/plan", json=_full_plan())
    status = client.get("/api/3d/finance/status")

    assert plan.status_code == 401
    assert status.status_code == 401


def test_finance_tools_use_trusted_identity_and_approval_boundary(finance_db, monkeypatch):
    from printing3d import create_order, settle_order
    import printing3d.tools as printing_tools
    from tool_registry import ToolRegistry

    class Call:
        user_id = "trusted-finance-owner"

    cfg = {"_universal_context": {"call": Call()}}
    plan = printing_tools.finance_plan_set(_full_plan(), cfg)
    assert plan["owner_id"] == "trusted-finance-owner"

    order = create_order(
        "trusted-finance-owner",
        {"title": "Tool profit", "quoted_price": 1000},
    )
    settle_order(
        "trusted-finance-owner",
        order["id"],
        actual_revenue=1000,
        actual_cost=200,
    )
    status = printing_tools.finance_status({"month": None}, cfg)
    assert status["cumulative_realized_profit"] == pytest.approx(800)

    registry = ToolRegistry()
    read_meta = registry.get_tool_meta("printing3d.finance.status")
    write_meta = registry.get_tool_meta("printing3d.finance.plan.set")
    assert read_meta["read_only"] is True
    assert read_meta["requires_approval"] is False
    assert write_meta["read_only"] is False
    assert write_meta["requires_approval"] is True

    monkeypatch.setattr(
        printing_tools,
        "get_current_owner_id",
        lambda required=True: "fallback-finance-owner",
    )
    fallback = printing_tools.finance_plan_set(_full_plan(), {})
    assert fallback["owner_id"] == "fallback-finance-owner"
