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

    treasury_tool = registry.get_tool_meta("printing3d.treasury.summary")
    orders_tool = registry.get_tool_meta("printing3d.orders.list")
    assert treasury_tool["read_only"] is True
    assert treasury_tool["requires_approval"] is False
    assert orders_tool["read_only"] is True
    assert orders_tool["requires_approval"] is False


def test_quote_tool_is_read_only_and_order_create_requires_approval():
    from tool_registry import ToolRegistry

    registry = ToolRegistry()
    quote_tool = registry.get_tool_meta("printing3d.quote")
    create_tool = registry.get_tool_meta("printing3d.order.create")

    assert quote_tool["read_only"] is True
    assert quote_tool["requires_approval"] is False
    assert create_tool["read_only"] is False
    assert create_tool["requires_approval"] is True


def test_quote_tool_reports_missing_cost_drivers_instead_of_zero_price():
    from printing3d.tools import quote

    result = quote({"material_grams": 120, "material_cost_per_kg": None, "print_hours": None})

    assert result == {
        "status": "needs_input",
        "missing_fields": ["material_cost_per_kg", "print_hours"],
    }


def test_quote_tool_matches_service_and_lists_assumed_defaults():
    from printing3d import calculate_quote
    from printing3d.tools import quote

    args = {
        "material_grams": 120,
        "material_cost_per_kg": 1800,
        "print_hours": 5,
        "electricity_cost_per_kwh": 7,
        "target_margin_percent": None,
    }
    result = quote(args)

    assert result["status"] == "ok"
    assert result["quote"] == calculate_quote(
        {
            "material_grams": 120,
            "material_cost_per_kg": 1800,
            "print_hours": 5,
            "electricity_cost_per_kwh": 7,
        }
    )
    assert result["quote"]["recommended_price"] > 0
    assert "electricity_cost_per_kwh" not in result["assumed_defaults"]
    assert "target_margin_percent" in result["assumed_defaults"]


def test_order_create_tool_recalculates_price_for_trusted_owner(printing_db):
    from printing3d import list_orders
    from printing3d.tools import order_create, quote

    class Call:
        user_id = "trusted-owner"

    quote_args = {"material_grams": 80, "material_cost_per_kg": 2000, "print_hours": 3}
    expected = quote(quote_args)["quote"]["recommended_price"]

    result = order_create(
        {"title": "Cable clip x10", "customer_name": "Ivan", "quote": quote_args},
        {"_universal_context": {"call": Call()}},
    )

    order = result["order"]
    assert order["owner_id"] == "trusted-owner"
    assert order["status"] == "quote"
    assert order["source"] == "alice"
    assert order["quoted_price"] == pytest.approx(expected)
    assert list_orders("other-owner") == []


def test_order_create_tool_rejects_unsafe_status_and_partial_quote(printing_db):
    from printing3d.tools import order_create

    with pytest.raises(ValueError, match="status"):
        order_create({"title": "X", "status": "paid"}, {})
    with pytest.raises(ValueError, match="print_hours"):
        order_create(
            {"title": "X", "quote": {"material_grams": 10, "material_cost_per_kg": 1000}},
            {},
        )


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


def test_create_order_rejects_paid_without_settlement(printing_db):
    from printing3d import create_order

    with pytest.raises(ValueError, match="requires settlement"):
        create_order("owner-a", {"title": "Impossible paid order", "status": "paid"})


def test_settlement_requires_cost_when_no_quote_exists(printing_db):
    from printing3d import create_order, settle_order

    order = create_order("owner-a", {"title": "Manual quote", "quoted_price": 500})

    with pytest.raises(ValueError, match="actual_cost is required"):
        settle_order("owner-a", order["id"], actual_revenue=500)


def test_cancelled_orders_do_not_inflate_quoted_revenue(printing_db):
    from printing3d import create_order, get_financial_summary, update_order_status

    active = create_order("owner-a", {"title": "Active", "quoted_price": 300})
    cancelled = create_order("owner-a", {"title": "Cancelled", "quoted_price": 700})
    update_order_status("owner-a", cancelled["id"], "cancelled")

    summary = get_financial_summary("owner-a")

    assert active["quoted_price"] == pytest.approx(300)
    assert summary["total_orders"] == 2
    assert summary["currencies"][0]["quoted_revenue"] == pytest.approx(300)
    assert summary["currencies"][0]["open_orders"] == 1


def test_quote_validation_edges():
    from printing3d import calculate_quote

    with pytest.raises(ValueError, match="object"):
        calculate_quote([])
    with pytest.raises(ValueError, match="must be numeric"):
        calculate_quote({"material_grams": "not-a-number"})
    with pytest.raises(ValueError, match="must be >= 0"):
        calculate_quote({"material_grams": -1})
    with pytest.raises(ValueError, match="failure_rate_percent must be < 100"):
        calculate_quote({"failure_rate_percent": 100})


def test_schema_migrates_legacy_print_orders(tmp_path, monkeypatch):
    import db
    import printing3d.service as service

    path = tmp_path / "printing3d-legacy.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setattr(service, "get_conn", db.get_conn)

    conn = db.get_conn()
    conn.execute(
        """
        CREATE TABLE print_orders (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            customer_name TEXT,
            source TEXT NOT NULL DEFAULT 'manual',
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'lead',
            currency TEXT NOT NULL DEFAULT 'RUB',
            quoted_price REAL,
            actual_revenue REAL,
            quote_json TEXT NOT NULL DEFAULT '{}',
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    service.init_3d_printing_tables()

    conn = db.get_conn()
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(print_orders)").fetchall()}
    conn.close()
    assert {"actual_cost", "settled_at"} <= columns


def test_order_row_recovers_from_invalid_quote_json(printing_db):
    import printing3d.service as service
    from printing3d import create_order, get_order

    order = create_order("owner-a", {"title": "Broken quote cache"})
    conn = service.get_conn()
    conn.execute("UPDATE print_orders SET quote_json = ? WHERE id = ?", ("{", order["id"]))
    conn.commit()
    conn.close()

    loaded = get_order("owner-a", order["id"])
    assert loaded["quote"] == {}
    assert "quote_json" not in loaded


def test_create_order_validation_and_actual_values(printing_db):
    from printing3d import create_order

    with pytest.raises(ValueError, match="owner identity"):
        create_order("", {"title": "No owner"})
    with pytest.raises(ValueError, match="object"):
        create_order("owner-a", [])
    with pytest.raises(ValueError, match="title is required"):
        create_order("owner-a", {})
    with pytest.raises(ValueError, match="invalid order status"):
        create_order("owner-a", {"title": "Bad status", "status": "mystery"})

    order = create_order(
        "owner-a",
        {
            "title": "Measured job",
            "actual_revenue": 420,
            "actual_cost": 125,
        },
    )
    assert order["actual_revenue"] == pytest.approx(420)
    assert order["actual_cost"] == pytest.approx(125)


def test_list_orders_status_filter_and_validation(printing_db):
    from printing3d import create_order, list_orders

    create_order("owner-a", {"title": "Lead"})
    create_order("owner-a", {"title": "Accepted", "status": "accepted"})

    accepted = list_orders("owner-a", status="accepted")
    assert [order["title"] for order in accepted] == ["Accepted"]

    with pytest.raises(ValueError, match="invalid order status"):
        list_orders("owner-a", status="unknown")


def test_update_order_status_rejects_unknown_status(printing_db):
    from printing3d import create_order, update_order_status

    order = create_order("owner-a", {"title": "Status validation"})
    with pytest.raises(ValueError, match="invalid order status"):
        update_order_status("owner-a", order["id"], "teleported")


def test_settlement_edge_cases_and_quote_defaults(printing_db):
    from printing3d import create_order, settle_order, update_order_status

    assert settle_order("owner-a", "missing-order", actual_revenue=1, actual_cost=1) is None

    quoted = create_order(
        "owner-a",
        {
            "title": "Quoted job",
            "quote": {
                "material_grams": 50,
                "material_cost_per_kg": 1000,
                "print_hours": 2,
                "depreciation_per_hour": 10,
                "target_margin_percent": 30,
            },
        },
    )
    settled = settle_order("owner-a", quoted["id"])
    assert settled["actual_revenue"] == pytest.approx(quoted["quoted_price"])
    assert settled["actual_cost"] == pytest.approx(quoted["quote"]["estimated_total_cost"])

    same = settle_order(
        "owner-a",
        quoted["id"],
        actual_revenue=settled["actual_revenue"],
        actual_cost=settled["actual_cost"],
    )
    assert same["settled_at"] == settled["settled_at"]

    with pytest.raises(ValueError, match="already settled"):
        settle_order(
            "owner-a",
            quoted["id"],
            actual_revenue=settled["actual_revenue"] + 1,
            actual_cost=settled["actual_cost"],
        )

    cancelled = create_order("owner-a", {"title": "Cancelled", "quoted_price": 100})
    update_order_status("owner-a", cancelled["id"], "cancelled")
    with pytest.raises(ValueError, match="cancelled order"):
        settle_order("owner-a", cancelled["id"], actual_revenue=100, actual_cost=20)

    no_price = create_order("owner-a", {"title": "No revenue"})
    with pytest.raises(ValueError, match="actual_revenue is required"):
        settle_order("owner-a", no_price["id"], actual_cost=20)


def test_printing3d_tool_helpers_cover_fallback_and_order_filters(printing_db, monkeypatch):
    from printing3d import create_order
    import printing3d.tools as printing_tools

    monkeypatch.setattr(
        printing_tools,
        "get_current_owner_id",
        lambda required=True: "fallback-owner",
    )
    create_order("fallback-owner", {"title": "Fallback lead"})
    create_order("fallback-owner", {"title": "Fallback accepted", "status": "accepted"})

    assert printing_tools._trusted_owner({}) == "fallback-owner"
    all_orders = printing_tools.orders_list({}, {})
    accepted = printing_tools.orders_list({"status": "accepted"}, {})

    assert len(all_orders["orders"]) == 2
    assert [order["title"] for order in accepted["orders"]] == ["Fallback accepted"]


def test_registry_survives_printing3d_tool_import_failure(monkeypatch):
    import builtins

    from tool_registry import ToolRegistry

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "printing3d.tools":
            raise ImportError("printing3d tool import test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    registry = ToolRegistry()

    assert registry.get_tool_meta("printing3d.treasury.summary") is None


@pytest.fixture
def printing_client(printing_db, monkeypatch):
    monkeypatch.setenv("ALICE_OWNER_ID", "route-owner")
    from app import app

    app.config.update(TESTING=True)
    return app.test_client()


def test_3d_routes_require_authenticated_owner(printing_db, monkeypatch):
    monkeypatch.delenv("ALICE_OWNER_ID", raising=False)
    from app import app

    app.config.update(TESTING=True)
    client = app.test_client()

    response = client.get("/api/3d/orders")
    assert response.status_code == 401
    assert response.get_json()["error"] == "authenticated owner identity is required"


def test_quote_routes_cover_success_and_validation(printing_client):
    invalid_body = printing_client.post("/api/3d/quote", json=[])
    assert invalid_body.status_code == 400
    assert invalid_body.get_json()["error"] == "request body must be an object"

    invalid_quote = printing_client.post("/api/3d/quote", json={"material_grams": -1})
    assert invalid_quote.status_code == 400
    assert invalid_quote.get_json()["error"] == "invalid quote parameters"

    valid = printing_client.post(
        "/api/3d/quote",
        json={"material_grams": 25, "material_cost_per_kg": 1000},
    )
    assert valid.status_code == 200
    assert valid.get_json()["material_cost"] == pytest.approx(25)


def test_order_collection_routes_cover_crud_and_validation(printing_client):
    empty = printing_client.get("/api/3d/orders")
    assert empty.status_code == 200
    assert empty.get_json() == {"orders": []}

    invalid_filter = printing_client.get("/api/3d/orders?status=unknown")
    assert invalid_filter.status_code == 400
    assert invalid_filter.get_json()["error"] == "invalid order status"

    invalid_body = printing_client.post("/api/3d/orders", json=[])
    assert invalid_body.status_code == 400
    assert invalid_body.get_json()["error"] == "request body must be an object"

    invalid_order = printing_client.post("/api/3d/orders", json={})
    assert invalid_order.status_code == 400
    assert invalid_order.get_json()["error"] == "invalid order"

    created = printing_client.post(
        "/api/3d/orders",
        json={"title": "Route order", "quoted_price": 500},
    )
    assert created.status_code == 201
    order = created.get_json()

    listed = printing_client.get("/api/3d/orders?status=lead")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.get_json()["orders"]] == [order["id"]]

    fetched = printing_client.get(f"/api/3d/orders/{order['id']}")
    assert fetched.status_code == 200
    assert fetched.get_json()["title"] == "Route order"

    missing = printing_client.get("/api/3d/orders/not-found")
    assert missing.status_code == 404
    assert missing.get_json()["error"] == "order not found"


def test_order_status_route_covers_success_errors_and_missing(printing_client):
    created = printing_client.post("/api/3d/orders", json={"title": "Status route"}).get_json()
    url = f"/api/3d/orders/{created['id']}/status"

    invalid_body = printing_client.patch(url, json=[])
    assert invalid_body.status_code == 400

    invalid_status = printing_client.patch(url, json={"status": "unknown"})
    assert invalid_status.status_code == 400
    assert invalid_status.get_json()["error"] == "invalid order status"

    accepted = printing_client.patch(url, json={"status": "accepted"})
    assert accepted.status_code == 200
    assert accepted.get_json()["status"] == "accepted"

    missing = printing_client.patch(
        "/api/3d/orders/not-found/status",
        json={"status": "accepted"},
    )
    assert missing.status_code == 404


def test_order_settlement_route_covers_success_validation_and_missing(printing_client):
    missing_body = printing_client.post("/api/3d/orders/not-found/settle")
    assert missing_body.status_code == 404

    created = printing_client.post(
        "/api/3d/orders",
        json={"title": "Settlement route", "quoted_price": 500},
    ).get_json()
    url = f"/api/3d/orders/{created['id']}/settle"

    invalid_body = printing_client.post(url, json=[])
    assert invalid_body.status_code == 400

    invalid_settlement = printing_client.post(url, json={"actual_revenue": 500})
    assert invalid_settlement.status_code == 400
    assert invalid_settlement.get_json()["error"] == "invalid settlement"

    settled = printing_client.post(
        url,
        json={"actual_revenue": 520, "actual_cost": 180},
    )
    assert settled.status_code == 200
    assert settled.get_json()["status"] == "paid"

    missing = printing_client.post(
        "/api/3d/orders/not-found/settle",
        json={"actual_revenue": 1, "actual_cost": 1},
    )
    assert missing.status_code == 404


def test_treasury_summary_route(printing_client):
    created = printing_client.post(
        "/api/3d/orders",
        json={"title": "Summary route", "quoted_price": 400},
    ).get_json()
    printing_client.post(
        f"/api/3d/orders/{created['id']}/settle",
        json={"actual_revenue": 450, "actual_cost": 150},
    )

    response = printing_client.get("/api/3d/treasury/summary")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total_orders"] == 1
    assert payload["currencies"][0]["settled_profit"] == pytest.approx(300)


def test_all_owner_scoped_3d_routes_reject_unauthenticated_requests(printing_db, monkeypatch):
    monkeypatch.delenv("ALICE_OWNER_ID", raising=False)
    from app import app

    app.config.update(TESTING=True)
    client = app.test_client()

    requests = [
        ("post", "/api/3d/orders", {"title": "Unauthorized"}),
        ("get", "/api/3d/orders/not-found", None),
        ("patch", "/api/3d/orders/not-found/status", {"status": "accepted"}),
        ("post", "/api/3d/orders/not-found/settle", {"actual_revenue": 1, "actual_cost": 1}),
        ("get", "/api/3d/treasury/summary", None),
    ]

    for method, path, payload in requests:
        response = getattr(client, method)(path, json=payload)
        assert response.status_code == 401
        assert response.get_json()["error"] == "authenticated owner identity is required"
