import pytest


def test_quote_calculates_cost_risk_fee_and_profit():
    from printing3d import calculate_quote

    quote = calculate_quote(
        {
            "material_grams": 100,
            "material_cost_per_kg": 1000,
            "print_hours": 5,
            "printer_power_watts": 120,
            "electricity_cost_per_kwh": 6,
            "depreciation_per_hour": 10,
            "packaging_cost": 20,
            "failure_rate_percent": 10,
            "platform_fee_percent": 5,
            "target_margin_percent": 30,
        }
    )

    assert quote["material_cost"] == pytest.approx(100)
    assert quote["electricity_cost"] == pytest.approx(3.6)
    assert quote["depreciation_cost"] == pytest.approx(50)
    assert quote["cost_with_risk"] == pytest.approx(190.96)
    assert quote["recommended_price"] == pytest.approx(293.78)
    assert quote["estimated_total_cost"] == pytest.approx(205.65)
    assert quote["expected_profit"] == pytest.approx(88.14)


def test_quote_rejects_impossible_margin_and_fee():
    from printing3d import calculate_quote

    with pytest.raises(ValueError, match="must be < 100"):
        calculate_quote(
            {
                "target_margin_percent": 80,
                "platform_fee_percent": 20,
            }
        )


@pytest.fixture
def printing_db(tmp_path, monkeypatch):
    import db
    import printing3d.service as service

    path = tmp_path / "printing3d.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setattr(service, "get_conn", db.get_conn)
    db.init_db()
    service.init_3d_printing_tables()
    return path


def test_orders_are_owner_scoped(printing_db):
    from printing3d import create_order, get_order, list_orders

    created = create_order(
        "owner-a",
        {
            "title": "Cable holder",
            "customer_name": "Customer",
            "quote": {
                "material_grams": 25,
                "material_cost_per_kg": 1000,
                "print_hours": 1,
                "target_margin_percent": 30,
            },
        },
    )

    assert get_order("owner-a", created["id"])["title"] == "Cable holder"
    assert get_order("owner-b", created["id"]) is None
    assert len(list_orders("owner-a")) == 1
    assert list_orders("owner-b") == []


def test_order_status_flow_requires_settlement_for_paid(printing_db):
    from printing3d import create_order, update_order_status

    order = create_order("owner-a", {"title": "Adapter"})
    accepted = update_order_status("owner-a", order["id"], "accepted")
    printing = update_order_status("owner-a", order["id"], "printing")

    assert accepted["status"] == "accepted"
    assert printing["status"] == "printing"
    with pytest.raises(ValueError, match="requires settlement"):
        update_order_status("owner-a", order["id"], "paid")


def test_settlement_updates_treasury_summary_without_mixing_owner_data(printing_db):
    from printing3d import create_order, get_financial_summary, settle_order

    order_a = create_order(
        "owner-a",
        {
            "title": "Meter bracket",
            "quoted_price": 500,
            "currency": "RUB",
        },
    )
    order_b = create_order(
        "owner-b",
        {
            "title": "Other owner order",
            "quoted_price": 900,
            "currency": "RUB",
        },
    )

    settled = settle_order(
        "owner-a",
        order_a["id"],
        actual_revenue=520,
        actual_cost=180,
    )
    settle_order(
        "owner-b",
        order_b["id"],
        actual_revenue=900,
        actual_cost=400,
    )

    assert settled["status"] == "paid"
    assert settled["actual_revenue"] == pytest.approx(520)
    assert settled["actual_cost"] == pytest.approx(180)
    assert settled["settled_at"]

    summary = get_financial_summary("owner-a")
    assert summary["total_orders"] == 1
    assert summary["currencies"] == [
        {
            "currency": "RUB",
            "orders": 1,
            "open_orders": 0,
            "paid_orders": 1,
            "quoted_revenue": 500.0,
            "settled_revenue": 520.0,
            "settled_cost": 180.0,
            "settled_profit": 340.0,
        }
    ]


def test_settlement_is_idempotent_when_retried_without_new_values(printing_db):
    from printing3d import create_order, settle_order

    order = create_order(
        "owner-a",
        {
            "title": "DIN rail clip",
            "quoted_price": 250,
        },
    )

    first = settle_order("owner-a", order["id"], actual_revenue=260, actual_cost=80)
    second = settle_order("owner-a", order["id"])

    assert second["settled_at"] == first["settled_at"]
    assert second["actual_revenue"] == pytest.approx(260)
    assert second["actual_cost"] == pytest.approx(80)


def test_printing3d_tools_are_registered_read_only():
    from tool_registry import ToolRegistry

    registry = ToolRegistry()
    definitions = {item["name"]: item for item in registry.get_definitions()}

    assert "printing3d.treasury.summary" in definitions
    assert "printing3d.orders.list" in definitions

    treasury_tool = registry.get_tool("printing3d.treasury.summary")
    orders_tool = registry.get_tool("printing3d.orders.list")
    assert treasury_tool["read_only"] is True
    assert treasury_tool["requires_approval"] is False
    assert orders_tool["read_only"] is True
    assert orders_tool["requires_approval"] is False


def test_treasury_summary_tool_uses_trusted_call_identity(printing_db):
    from printing3d import create_order, settle_order
    from printing3d.tools import treasury_summary

    order = create_order("trusted-owner", {"title": "Socket holder", "quoted_price": 300})
    settle_order(
        "trusted-owner",
        order["id"],
        actual_revenue=320,
        actual_cost=100,
    )
    other = create_order("other-owner", {"title": "Hidden order", "quoted_price": 999})
    settle_order(
        "other-owner",
        other["id"],
        actual_revenue=999,
        actual_cost=1,
    )

    class Call:
        user_id = "trusted-owner"

    result = treasury_summary({}, {"_universal_context": {"call": Call()}})

    assert result["total_orders"] == 1
    assert result["currencies"][0]["settled_profit"] == pytest.approx(220)
