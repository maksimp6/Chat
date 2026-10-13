"""Domain services for the Alice Pro 3D-printing business MVP."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from db import get_store

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
    get_store()


def _row_to_order(row) -> dict:
    if not isinstance(row.get("quote"), dict):
        raise ValueError("invalid native order record")
    return row


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
    if status == "paid":
        raise ValueError("paid status requires settlement")

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
    record = {
        "id": order_id,
        "owner_id": owner_id,
        "customer_name": str(data.get("customer_name") or "").strip() or None,
        "source": str(data.get("source") or "manual").strip() or "manual",
        "title": title,
        "status": status,
        "currency": str(data.get("currency") or quote.get("currency") or "RUB"),
        "quoted_price": quoted_price,
        "actual_revenue": actual_revenue,
        "actual_cost": actual_cost,
        "quote": quote,
        "notes": str(data.get("notes") or "").strip() or None,
        "settled_at": None,
        "created_at": now,
        "updated_at": now,
    }
    with get_store().transaction() as tx:
        tx.set("printing/order/" + order_id, record)
    return get_order(owner_id, order_id)


def get_order(owner_id: str, order_id: str) -> dict | None:
    row = get_store().get("printing/order/" + order_id)
    return _row_to_order(row) if row is not None and row["owner_id"] == owner_id else None


def list_orders(owner_id: str, *, status: str | None = None) -> list[dict]:
    if status and status not in ORDER_STATUSES:
        raise ValueError("invalid order status")
    rows = [
        row
        for row in get_store().items(prefix="printing/order/").values()
        if row["owner_id"] == owner_id and (not status or row["status"] == status)
    ]
    return sorted(rows, key=lambda row: row["created_at"], reverse=True)


def update_order_status(owner_id: str, order_id: str, status: str) -> dict | None:
    if status not in ORDER_STATUSES:
        raise ValueError("invalid order status")
    if status == "paid":
        raise ValueError("paid status requires settlement")
    key = "printing/order/" + order_id
    with get_store().transaction() as tx:
        row = tx.get(key)
        if row is not None and row["owner_id"] == owner_id:
            tx.set(key, {**row, "status": status, "updated_at": datetime.utcnow().isoformat()})
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

    if order.get("status") == "cancelled":
        raise ValueError("cancelled order cannot be settled")

    revenue_source = actual_revenue
    if revenue_source is None:
        revenue_source = order.get("quoted_price")
    if revenue_source is None:
        raise ValueError("actual_revenue is required")

    quote = order.get("quote") or {}
    cost_source = actual_cost
    if cost_source is None:
        cost_source = quote.get("estimated_total_cost")
    if cost_source is None:
        raise ValueError("actual_cost is required")

    revenue = _money(_decimal(revenue_source, "actual_revenue"))
    cost = _money(_decimal(cost_source, "actual_cost"))
    now = datetime.utcnow().isoformat()

    _publish_settlement(owner_id, order_id, revenue, cost, now)
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
        if order.get("status") != "cancelled" and quoted_price is not None:
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


def _publish_settlement(
    owner_id: str, order_id: str, revenue: float, cost: float, now: str
) -> None:
    key = "printing/order/" + order_id
    with get_store().transaction() as tx:
        current = tx.get(key)
        if current is None or current["owner_id"] != owner_id:
            return None
        if current.get("settled_at"):
            if current["actual_revenue"] != revenue or current["actual_cost"] != cost:
                raise ValueError("order is already settled")
        elif current["status"] == "cancelled":
            raise ValueError("cancelled order cannot be settled")
        else:
            tx.set(
                key,
                {
                    **current,
                    "status": "paid",
                    "actual_revenue": revenue,
                    "actual_cost": cost,
                    "settled_at": now,
                    "updated_at": now,
                },
            )


def record_printer_downtime(
    owner_id: str,
    printer_id: str,
    state: str,
    timestamp: float,
    *,
    reason: str | None = None,
    event_id: str | None = None,
) -> bool:
    """Commit one trusted observation to canonical shared MemoryStore atomically.

    This is a backend API, not a publicly exposed telemetry endpoint.
    """
    if not owner_id or not printer_id or "/" in printer_id:
        raise ValueError("owner and printer are required")
    from .downtime import DowntimeTracker

    key = "printing/downtime/" + owner_id + "/" + printer_id
    with get_store().transaction() as tx:
        snapshot = tx.get(key)
        tracker = DowntimeTracker.from_snapshot(snapshot) if snapshot else DowntimeTracker()
        changed = tracker.observe(state, timestamp, reason=reason, event_id=event_id)
        if changed:
            tx.set(key, tracker.snapshot())
    return changed


def get_printer_downtime(owner_id: str, printer_id: str, start: float, end: float) -> dict[str, object]:
    """Read committed downtime intervals for this owner/printer."""
    if not owner_id or not printer_id or "/" in printer_id:
        raise ValueError("owner and printer are required")
    from .downtime import DowntimeTracker

    snapshot = get_store().get("printing/downtime/" + owner_id + "/" + printer_id)
    tracker = DowntimeTracker.from_snapshot(snapshot) if snapshot else DowntimeTracker()
    return tracker.summary(start, end)
