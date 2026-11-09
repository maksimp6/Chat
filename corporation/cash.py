"""The cash desk: revenue and expenses, counted from the ledger, never kept apart from it.

Revenue is ``revenue.confirmed`` records: payments the channels saw (Telegram
Payments) or the owner's word for a sale outside the channels ("корпорация:
продано"). Expenses are the owner's lines ("корпорация: расход") plus the box's
electricity, estimated from the cycle's running time at 7 W.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from corporation import ledger as journal

__all__ = ["confirm_sale", "expense", "summary"]

WATTS = 7.0
RUB_PER_KWH = 6.0


def _hours_running(st: journal.State) -> float:
    since = st.cycle.get("since")
    if not st.cycle.get("running") or not since:
        return 0.0
    try:
        started = datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return 0.0
    return max(0.0, (datetime.now(UTC) - started).total_seconds() / 3600)


def summary(st: journal.State | None = None) -> dict[str, Any]:
    st = st or journal.state()
    power = _hours_running(st) * WATTS / 1000 * RUB_PER_KWH
    by_product: dict[str, float] = {}
    for sale in st.money["sales"]:
        key = str(sale.get("product") or "?")
        by_product[key] = by_product.get(key, 0.0) + float(sale.get("amount") or 0)
    return {
        "proposed": round(st.money["proposed"], 2),
        "confirmed": round(st.money["confirmed"], 2),
        "expenses": round(st.money["expenses"] + power, 2),
        "power_rub": round(power, 2),
        "net": round(st.money["confirmed"] - st.money["expenses"] - power, 2),
        "sales": len(st.money["sales"]),
        "by_product": by_product,
        "currency": "RUB",
    }


def confirm_sale(
    product: str, amount: float, note: str = "", client: str | None = None
) -> dict[str, Any]:
    st = journal.state()
    if product not in st.products:
        raise ValueError(f"no such product: {product}")
    entry = journal.ledger().append(
        "revenue.confirmed",
        "owner",
        {
            "amount": float(amount),
            "currency": "RUB",
            "product": product,
            "client": client,
            "channel": "owner",
            "note": note[:1000],
        },
        product=product,
        project=st.products[product]["project"],
    )
    return {
        "seq": entry["seq"],
        "confirmed": round(st.money["confirmed"] + float(amount), 2),
    }


def expense(amount: float, what: str) -> dict[str, Any]:
    entry = journal.ledger().append(
        "expense",
        "owner",
        {"amount": float(amount), "currency": "RUB", "what": what[:1000]},
    )
    return {"seq": entry["seq"]}
