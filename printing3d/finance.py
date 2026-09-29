"""AI-first credit and payback assessment for the 3D-printing business."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _number(
    data: Mapping[str, Any],
    name: str,
    default: int | float = 0,
    *,
    minimum: Decimal = Decimal("0"),
) -> Decimal:
    raw = data.get(name, default)
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _whole_months(data: Mapping[str, Any], name: str, default: int = 0) -> int:
    value = _number(data, name, default)
    if value != value.to_integral_value():
        raise ValueError(f"{name} must be a whole number")
    return int(value)


def calculate_financing_plan(data: Mapping[str, Any]) -> dict[str, Any]:
    """Calculate credit burden and business payback without choosing a lender."""
    if not isinstance(data, Mapping):
        raise ValueError("financing input must be an object")

    equipment_price = _number(data, "equipment_price")
    if equipment_price <= 0:
        raise ValueError("equipment_price must be > 0")

    startup_costs = _number(data, "startup_costs")
    financed_principal = _number(data, "financed_principal")
    monthly_payment = _number(data, "monthly_payment")
    term_months = _whole_months(data, "term_months")
    planned_monthly_profit = _number(data, "planned_monthly_profit")
    target_coverage_raw = data.get("target_payment_coverage")
    target_coverage = (
        Decimal("2")
        if target_coverage_raw is None
        else _number(data, "target_payment_coverage", minimum=Decimal("1"))
    )
    psk_percent = data.get("psk_percent")
    if psk_percent is not None:
        psk_percent = _number(data, "psk_percent")

    if financed_principal > equipment_price:
        raise ValueError("financed_principal cannot exceed equipment_price")

    if financed_principal > 0:
        if monthly_payment <= 0 or term_months <= 0:
            raise ValueError("credit requires monthly_payment and term_months")
        total_credit_repayment = monthly_payment * term_months
        if total_credit_repayment < financed_principal:
            raise ValueError("total credit repayment cannot be below financed principal")
    else:
        if monthly_payment != 0 or term_months != 0:
            raise ValueError("cash purchase cannot have credit payment or term")
        total_credit_repayment = Decimal("0")

    initial_cash_required = equipment_price - financed_principal + startup_costs
    financing_cost = total_credit_repayment - financed_principal
    total_business_outlay = initial_cash_required + total_credit_repayment
    required_monthly_profit = monthly_payment * target_coverage

    payment_coverage_ratio = None
    payment_coverage_ok = None
    if monthly_payment > 0:
        payment_coverage_ratio = planned_monthly_profit / monthly_payment
        payment_coverage_ok = planned_monthly_profit >= required_monthly_profit

    payback_months = None
    initial_cash_payback_months = None
    if planned_monthly_profit > 0:
        payback_months = total_business_outlay / planned_monthly_profit
        initial_cash_payback_months = initial_cash_required / planned_monthly_profit

    credit_share = financed_principal / equipment_price * Decimal("100")

    return {
        "currency": str(data.get("currency") or "RUB"),
        "equipment_price": _money(equipment_price),
        "startup_costs": _money(startup_costs),
        "financed_principal": _money(financed_principal),
        "credit_share_percent": _money(credit_share),
        "monthly_payment": _money(monthly_payment),
        "term_months": term_months,
        "psk_percent": _money(psk_percent) if psk_percent is not None else None,
        "total_credit_repayment": _money(total_credit_repayment),
        "financing_cost": _money(financing_cost),
        "initial_cash_required": _money(initial_cash_required),
        "total_business_outlay": _money(total_business_outlay),
        "planned_monthly_profit": _money(planned_monthly_profit),
        "target_payment_coverage": _money(target_coverage),
        "required_monthly_profit": _money(required_monthly_profit),
        "planned_surplus_after_payment": _money(planned_monthly_profit - monthly_payment),
        "payment_coverage_ratio": (
            _money(payment_coverage_ratio) if payment_coverage_ratio is not None else None
        ),
        "payment_coverage_ok": payment_coverage_ok,
        "payback_months": _money(payback_months) if payback_months is not None else None,
        "initial_cash_payback_months": (
            _money(initial_cash_payback_months)
            if initial_cash_payback_months is not None
            else None
        ),
    }


def assess_financing_plan(
    data: Mapping[str, Any],
    *,
    observed_monthly_profit: float = 0,
) -> dict[str, Any]:
    """Accept sparse AI-collected terms and explain what remains unknown."""
    if not isinstance(data, Mapping):
        raise ValueError("financing input must be an object")

    purchase_mode = str(data.get("purchase_mode") or "credit").strip().lower()
    if purchase_mode not in {"credit", "cash"}:
        raise ValueError("purchase_mode must be credit or cash")

    currency = str(data.get("currency") or "RUB")
    inputs = {
        "equipment_price": data.get("equipment_price"),
        "startup_costs": data.get("startup_costs"),
        "financed_principal": data.get("financed_principal"),
        "monthly_payment": data.get("monthly_payment"),
        "term_months": data.get("term_months"),
        "planned_monthly_profit": data.get("planned_monthly_profit"),
        "target_payment_coverage": data.get("target_payment_coverage"),
        "psk_percent": data.get("psk_percent"),
        "currency": currency,
    }

    missing_fields = []
    if inputs["equipment_price"] is None:
        missing_fields.append("equipment_price")

    if purchase_mode == "credit":
        for name in ("financed_principal", "monthly_payment", "term_months"):
            if inputs[name] is None:
                missing_fields.append(name)
    else:
        inputs["financed_principal"] = 0
        inputs["monthly_payment"] = 0
        inputs["term_months"] = 0

    assumptions = []
    if inputs["startup_costs"] is None:
        inputs["startup_costs"] = 0
        assumptions.append("startup_costs=0")

    profit_source = "provided"
    if inputs["planned_monthly_profit"] is None:
        inputs["planned_monthly_profit"] = observed_monthly_profit
        profit_source = "realized_3d_profit_last_30_days"
        assumptions.append("planned_monthly_profit=observed_30d_profit")

    if inputs["target_payment_coverage"] is None:
        inputs["target_payment_coverage"] = 2
        assumptions.append("target_payment_coverage=2")

    assessment = {
        "status": "needs_terms" if missing_fields else "ready",
        "purchase_mode": purchase_mode,
        "currency": currency,
        "missing_fields": missing_fields,
        "assumptions": assumptions,
        "profit_source": profit_source,
        "observed_monthly_profit": _money(Decimal(str(observed_monthly_profit))),
        "known_inputs": inputs,
        "plan": None,
    }
    if missing_fields:
        return assessment

    assessment["plan"] = calculate_financing_plan(inputs)
    return assessment
