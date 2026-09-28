"""Normalize Cloud.ru billing consumption payloads into one spend total.

The Cloud.ru billing API (``organization.api.cloud.ru``, methods
``v1/consumption`` and ``v2/consumption``) returns consumption rows. Field
names differ between API versions, so the parser accepts the common shapes
and reports ``total_cost=None`` instead of guessing when nothing matches.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

_TOTAL_KEYS = ("total_cost", "total", "total_amount", "cost_total")
_ROW_LIST_KEYS = ("items", "data", "consumption", "rows", "result")
_ROW_COST_KEYS = ("cost", "total_cost", "amount", "sum", "price", "total")
_CURRENCY_KEYS = ("currency", "currency_code")


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, dict):
        for key in ("value", "amount"):
            if key in value:
                return _decimal(value[key])
        return None
    try:
        amount = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() else None


def _currency(payload: dict[str, Any]) -> str | None:
    for key in _CURRENCY_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return None


def parse_consumption_total(payload: Any) -> dict[str, Any]:
    """Return ``{"total_cost", "currency", "rows"}`` for a billing payload."""
    if not isinstance(payload, dict):
        return {"total_cost": None, "currency": None, "rows": 0}

    currency = _currency(payload)
    for key in _TOTAL_KEYS:
        value = payload.get(key)
        total = _decimal(value)
        if total is not None:
            nested = _currency(value) if isinstance(value, dict) else None
            return {"total_cost": total, "currency": nested or currency, "rows": 0}

    summary = payload.get("summary")
    if isinstance(summary, dict):
        nested = parse_consumption_total(summary)
        if nested["total_cost"] is not None:
            nested["currency"] = nested["currency"] or currency
            return nested

    for list_key in _ROW_LIST_KEYS:
        rows = payload.get(list_key)
        if not isinstance(rows, list):
            continue
        total = Decimal("0")
        counted = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            for cost_key in _ROW_COST_KEYS:
                value = row.get(cost_key)
                amount = _decimal(value)
                if amount is not None:
                    total += amount
                    counted += 1
                    nested = _currency(value) if isinstance(value, dict) else None
                    currency = currency or nested or _currency(row)
                    break
        if counted:
            return {"total_cost": total, "currency": currency, "rows": counted}

    return {"total_cost": None, "currency": currency, "rows": 0}
