"""Domain services for the Alice Pro 3D-printing business MVP."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from db import get_conn

ORDER_STATUSES = (
    "lead",
    "quote",
    "accepted",
    "printing",
    "ready",
    "delivered",
    "paid",
    "cancelled",
)


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _decimal(value, name: str, *, minimum: str = "0") -> Decimal:
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if number < Decimal(minimum):
        raise ValueError(f"{name} must be >= {minimum}")
    return number


def calculate_quote(data: dict) -> dict:
    """Calculate a conservative quote from material, machine and selling costs."""
    if not isinstance(data, dict):
        raise ValueError("quote input must be an object")

    grams = _decimal(data.get("material_grams", 0), "material_grams")
    material_cost_per_kg = _decimal(data.get("material_cost_per_kg", 0), "material_cost_per_kg")
    print_hours = _decimal(data.get("print_hours", 0), "print_hours")
    power_watts = _decimal(data.get("printer_power_watts", 120), "printer_power_watts")
    electricity_per_kwh = _decimal(
        data.get("electricity_cost_per_kwh", 0), "electricity_cost_per_kwh"
    )
    depreciation_per_hour = _decimal(data.get("depreciation_per_hour", 0), "depreciation_per_hour")
    packaging_cost = _decimal(data.get("packaging_cost", 0), "packaging_cost")
    failure_rate = _decimal(data.get("failure_rate_percent", 10), "failure_rate_percent")
    platform_fee = _decimal(data.get("platform_fee_percent", 0), "platform_fee_percent")
    margin = _decimal(data.get("target_margin_percent", 30), "target_margin_percent")

    if failure_rate >= 100:
        raise ValueError("failure_rate_percent must be < 100")
    if platform_fee + margin >= 100:
        raise ValueError("platform_fee_percent + target_margin_percent must be < 100")

    material = grams / Decimal("1000") * material_cost_per_kg
    electricity = print_hours * power_watts / Decimal("1000") * electricity_per_kwh
    depreciation = print_hours * depreciation_per_hour
    direct_cost = material + electricity + depreciation + packaging_cost
    failure_reserve = direct_cost * failure_rate / Decimal("100")
    cost_with_risk = direct_cost + failure_reserve

    denominator = Decimal("1") - (platform_fee + margin) / Decimal("100")
    recommended_price = cost_with_risk / denominator if denominator else cost_with_risk
    expected_platform_fee = recommended_price * platform_fee / Decimal("100")
    estimated_total_cost = cost_with_risk + expected_platform_fee
    expected_profit = recommended_price - estimated_total_cost

    return {
        "material_cost": _money(material),
        "electricity_cost": _money(electricity),
        "depreciation_cost": _money(depreciation),
        "packaging_cost": _money(packaging_cost),
        "failure_reserve": _money(failure_reserve),
        "cost_with_risk": _money(cost_with_risk),
        "recommended_price": _money(recommended_price),
        "expected_platform_fee": _money(expected_platform_fee),
        "estimated_total_cost": _money(estimated_total_cost),
        "expected_profit": _money(expected_profit),
        "currency": str(data.get("currency") or "RUB"),
    }


def init_3d_printing_tables() -> None:
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS print_orders (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            customer_name TEXT,
            source TEXT NOT NULL DEFAULT 'manual',
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'lead',
            currency TEXT NOT NULL DEFAULT 'RUB',
            quoted_price REAL,
            actual_revenue REAL,
            actual_cost REAL,
            quote_json TEXT NOT NULL DEFAULT '{}',
            notes TEXT,
            settled_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(status IN (
                'lead', 'quote', 'accepted', 'printing',
                'ready', 'delivered', 'paid', 'cancelled'
            ))
        )
        """
    )
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(print_orders)").fetchall()}
    if "actual_cost" not in columns:
        conn.execute("ALTER TABLE print_orders ADD COLUMN actual_cost REAL")
    if "settled_at" not in columns:
        conn.execute("ALTER TABLE print_orders ADD COLUMN settled_at TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_print_orders_owner_created "
        "ON print_orders(owner_id, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_print_orders_owner_status ON print_orders(owner_id, status)"
    )
    conn.commit()
    conn.close()


def _row_to_order(row) -> dict:
    result = dict(row)
    try:
        result["quote"] = json.loads(result.pop("quote_json") or "{}")
    except Exception:
        result["quote"] = {}
        result.pop("quote_json", None)
    return result


def create_order(owner_id: str, data: dict) -> dict:
    if not owner_id:
        raise ValueError("owner identity is required")
    if not isinstance(data, dict):
        raise ValueError("order input must be an object")

    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")

    status = str(data.get("status") or "lead").strip()
    if status not in ORDER_STATUSES:
        raise ValueError("invalid order status")

    quote_input = data.get("quote")
    quote = calculate_quote(quote_input) if isinstance(quote_input, dict) else {}
    quoted_price = data.get("quoted_price")
    if quoted_price is None and quote:
        quoted_price = quote["recommended_price"]
    if quoted_price is not None:
        quoted_price = float(_decimal(quoted_price, "quoted_price"))

    actual_revenue = data.get("actual_revenue")
    if actual_revenue is not None:
        actual_revenue = float(_decimal(actual_revenue, "actual_revenue"))

    actual_cost = data.get("actual_cost")
    if actual_cost is not None:
        actual_cost = float(_decimal(actual_cost, "actual_cost"))

    now = datetime.utcnow().isoformat()
    order_id = str(uuid4())
    init_3d_printing_tables()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO print_orders(
            id, owner_id, customer_name, source, title, status, currency,
            quoted_price, actual_revenue, actual_cost, quote_json, notes,
            settled_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_id,
            owner_id,
            str(data.get("customer_name") or "").strip() or None,
            str(data.get("source") or "manual").strip() or "manual",
            title,
            status,
            str(data.get("currency") or quote.get("currency") or "RUB"),
            quoted_price,
            actual_revenue,
            actual_cost,
            json.dumps(quote, ensure_ascii=False),
            str(data.get("notes") or "").strip() or None,
            None,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return get_order(owner_id, order_id)


def get_order(owner_id: str, order_id: str) -> dict | None:
    init_3d_printing_tables()
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM print_orders WHERE id = ? AND owner_id = ?",
        (order_id, owner_id),
    ).fetchone()
    conn.close()
    return _row_to_order(row) if row else None


def list_orders(owner_id: str, *, status: str | None = None) -> list[dict]:
    init_3d_printing_tables()
    conn = get_conn()
    if status:
        if status not in ORDER_STATUSES:
            conn.close()
            raise ValueError("invalid order status")
        rows = conn.execute(
            "SELECT * FROM print_orders WHERE owner_id = ? AND status = ? ORDER BY created_at DESC",
            (owner_id, status),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM print_orders WHERE owner_id = ? ORDER BY created_at DESC",
            (owner_id,),
        ).fetchall()
    conn.close()
    return [_row_to_order(row) for row in rows]


def update_order_status(owner_id: str, order_id: str, status: str) -> dict | None:
    if status not in ORDER_STATUSES:
        raise ValueError("invalid order status")
    if status == "paid":
        raise ValueError("paid status requires settlement")
    init_3d_printing_tables()
    conn = get_conn()
    now = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE print_orders SET status = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
        (status, now, order_id, owner_id),
    )
    conn.commit()
    conn.close()
    return get_order(owner_id, order_id)


def settle_order(
    owner_id: str,
    order_id: str,
    *,
    actual_revenue=None,
    actual_cost=None,
) -> dict | None:
    """Finalize an order with actual revenue/cost values used by Treasury reporting."""
    order = get_order(owner_id, order_id)
    if order is None:
        return None

    if order.get("settled_at"):
        if actual_revenue is None and actual_cost is None:
            return order
        revenue = _money(_decimal(actual_revenue, "actual_revenue"))
        cost = _money(_decimal(actual_cost, "actual_cost"))
        if revenue == order.get("actual_revenue") and cost == order.get("actual_cost"):
            return order
        raise ValueError("order is already settled")

    revenue_source = actual_revenue
    if revenue_source is None:
        revenue_source = order.get("quoted_price")
    if revenue_source is None:
        raise ValueError("actual_revenue is required")

    quote = order.get("quote") or {}
    cost_source = actual_cost
    if cost_source is None:
        cost_source = quote.get("estimated_total_cost", 0)

    revenue = _money(_decimal(revenue_source, "actual_revenue"))
    cost = _money(_decimal(cost_source, "actual_cost"))
    now = datetime.utcnow().isoformat()

    conn = get_conn()
    conn.execute(
        """
        UPDATE print_orders
           SET status = 'paid',
               actual_revenue = ?,
               actual_cost = ?,
               settled_at = ?,
               updated_at = ?
         WHERE id = ? AND owner_id = ?
        """,
        (revenue, cost, now, now, order_id, owner_id),
    )
    conn.commit()
    conn.close()
    return get_order(owner_id, order_id)


def get_financial_summary(owner_id: str) -> dict:
    """Return Treasury-facing 3D-printing P&L grouped by currency."""
    orders = list_orders(owner_id)
    by_currency: dict[str, dict] = {}

    for order in orders:
        currency = str(order.get("currency") or "RUB")
        bucket = by_currency.setdefault(
            currency,
            {
                "currency": currency,
                "orders": 0,
                "open_orders": 0,
                "paid_orders": 0,
                "quoted_revenue": Decimal("0"),
                "settled_revenue": Decimal("0"),
                "settled_cost": Decimal("0"),
                "settled_profit": Decimal("0"),
            },
        )
        bucket["orders"] += 1

        quoted_price = order.get("quoted_price")
        if quoted_price is not None:
            bucket["quoted_revenue"] += Decimal(str(quoted_price))

        if order.get("status") == "paid" and order.get("settled_at"):
            revenue = Decimal(str(order.get("actual_revenue") or 0))
            cost = Decimal(str(order.get("actual_cost") or 0))
            bucket["paid_orders"] += 1
            bucket["settled_revenue"] += revenue
            bucket["settled_cost"] += cost
            bucket["settled_profit"] += revenue - cost
        elif order.get("status") != "cancelled":
            bucket["open_orders"] += 1

    currencies = []
    for currency in sorted(by_currency):
        bucket = by_currency[currency]
        currencies.append(
            {
                **bucket,
                "quoted_revenue": _money(bucket["quoted_revenue"]),
                "settled_revenue": _money(bucket["settled_revenue"]),
                "settled_cost": _money(bucket["settled_cost"]),
                "settled_profit": _money(bucket["settled_profit"]),
            }
        )

    return {
        "total_orders": len(orders),
        "currencies": currencies,
    }
