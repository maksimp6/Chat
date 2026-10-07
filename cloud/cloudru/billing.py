"""Normalize Cloud.ru billing consumption payloads into one spend total.

The Cloud.ru billing API (``organization.api.cloud.ru``, methods
``v1/consumption`` and ``v2/consumption``) returns consumption rows. Field
names differ between API versions, so the parser accepts the common shapes
and reports ``total_cost=None`` instead of guessing when nothing matches.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

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
        row_currencies: set[str] = set()
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
                    row_currency = nested or _currency(row)
                    if row_currency:
                        row_currencies.add(row_currency)
                    break
        if counted:
            if currency:
                row_currencies.add(currency)
            if len(row_currencies) > 1:
                # A sum across currencies is meaningless; report it as unparseable.
                return {"total_cost": None, "currency": None, "rows": counted}
            currency = next(iter(row_currencies), None)
            return {"total_cost": total, "currency": currency, "rows": counted}

    return {"total_cost": None, "currency": currency, "rows": 0}


def _validated_decimal_fields(values: Mapping[str, Any]) -> dict[str, Decimal]:
    normalized: dict[str, Decimal] = {}
    for name, value in values.items():
        amount = _decimal(value)
        if amount is None or amount < 0:
            raise ValueError(f"{name} must be a non-negative finite decimal")
        normalized[name] = amount
    return normalized


def estimate_container_runtime_cost(
    *,
    active_seconds: int,
    idle_seconds: int,
    cold_starts: int,
    vcpu: Decimal,
    memory_gb: Decimal,
    vcpu_rub_per_hour: Decimal,
    memory_rub_per_gb_hour: Decimal,
) -> dict[str, Any]:
    """Estimate gross Container Apps runtime cost from measured runtime seconds.

    This helper deliberately does not apply organization-level free tier because
    free-tier allocation is shared across services and must be reconciled against
    provider billing rather than guessed per container.
    """

    integer_fields = {
        "active_seconds": active_seconds,
        "idle_seconds": idle_seconds,
        "cold_starts": cold_starts,
    }
    for name, value in integer_fields.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")

    decimal_fields = {
        "vcpu": vcpu,
        "memory_gb": memory_gb,
        "vcpu_rub_per_hour": vcpu_rub_per_hour,
        "memory_rub_per_gb_hour": memory_rub_per_gb_hour,
    }
    normalized = _validated_decimal_fields(decimal_fields)

    billable_seconds = active_seconds + idle_seconds
    hours = Decimal(billable_seconds) / Decimal(3600)
    quantum = Decimal("0.0000001")
    cpu_rub = (hours * normalized["vcpu"] * normalized["vcpu_rub_per_hour"]).quantize(quantum)
    memory_rub = (hours * normalized["memory_gb"] * normalized["memory_rub_per_gb_hour"]).quantize(
        quantum
    )

    return {
        "status": "estimated",
        "active_seconds": active_seconds,
        "idle_seconds": idle_seconds,
        "billable_seconds": billable_seconds,
        "cold_starts": cold_starts,
        "cpu_rub": cpu_rub,
        "memory_rub": memory_rub,
        "estimated_rub": (cpu_rub + memory_rub).quantize(quantum),
        "free_tier_applied": False,
    }


def reconcile_container_runtime_cost(
    *, estimated_rub: Decimal, actual_rub: Decimal | None
) -> dict[str, Any]:
    """Compare a gross runtime estimate with provider-measured billing."""
    estimated = _decimal(estimated_rub)
    if estimated is None or estimated < 0:
        raise ValueError("estimated_rub must be a non-negative finite decimal")
    if actual_rub is None:
        return {
            "status": "unknown",
            "estimated_rub": estimated,
            "actual_rub": None,
            "variance_rub": None,
            "variance_percent": None,
        }

    actual = _decimal(actual_rub)
    if actual is None or actual < 0:
        raise ValueError("actual_rub must be a non-negative finite decimal")

    variance = (actual - estimated).quantize(Decimal("0.0000001"))
    variance_percent = None
    if estimated:
        variance_percent = (
            variance / estimated * Decimal(100)
        ).quantize(Decimal("0.0001"))

    return {
        "status": "measured",
        "estimated_rub": estimated,
        "actual_rub": actual,
        "variance_rub": variance,
        "variance_percent": variance_percent,
    }


def detect_unexpected_hot(
    *,
    idle_seconds: int,
    configured_idle_timeout_seconds: int,
    running_instances: int,
) -> bool:
    """Return true when a running instance outlives its configured idle budget."""
    values = {
        "idle_seconds": (idle_seconds, 0),
        "configured_idle_timeout_seconds": (configured_idle_timeout_seconds, 1),
        "running_instances": (running_instances, 0),
    }
    for name, (value, minimum) in values.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{name} is outside the allowed range")
    return running_instances > 0 and idle_seconds > configured_idle_timeout_seconds
