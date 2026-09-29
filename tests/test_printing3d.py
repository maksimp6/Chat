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
    assert quote["expected_profit"] == pytest.approx(88.13)


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


def test_order_status_flow(printing_db):
    from printing3d import create_order, update_order_status

    order = create_order("owner-a", {"title": "Adapter"})
    accepted = update_order_status("owner-a", order["id"], "accepted")
    printing = update_order_status("owner-a", order["id"], "printing")

    assert accepted["status"] == "accepted"
    assert printing["status"] == "printing"
