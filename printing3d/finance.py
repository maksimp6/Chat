"""Financing and payback tracking for the Logos 3D-printing business."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from printing3d.money import decimal_field, money_float
from printing3d.store import PrintStore

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _optional_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if str(number) != str(value).strip() and not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if number < 1:
        raise ValueError(f"{name} must be >= 1")
    return number


def _plan_row(row) -> dict[str, Any]:
    return {
        "owner_id": row["owner_id"],
        "printer_model": row["printer_model"],
        "equipment_price": row["equipment_price"],
        "setup_cost": row["setup_cost"],
        "down_payment": row["down_payment"],
        "credit_principal": row["credit_principal"],
        "credit_total_repayment": row["credit_total_repayment"],
        "monthly_payment": row["monthly_payment"],
        "term_months": row["term_months"],
        "psk_percent": row["psk_percent"],
        "currency": row["currency"],
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
    }


def set_financing_plan(
    store: PrintStore, owner_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("finance plan must be an object")

    model = str(data.get("printer_model") or "").strip()
    if not model:
        raise ValueError("printer_model is required")

    equipment_price = decimal_field(data.get("equipment_price"), "equipment_price")
    setup_cost = decimal_field(data.get("setup_cost", 0), "setup_cost")
    down_payment = decimal_field(data.get("down_payment", 0), "down_payment")
    credit_principal = decimal_field(
        data.get("credit_principal", 0), "credit_principal"
    )
    total_repayment = decimal_field(
        data.get("credit_total_repayment"),
        "credit_total_repayment",
        nullable=True,
    )
    monthly_payment = decimal_field(
        data.get("monthly_payment"),
        "monthly_payment",
        nullable=True,
    )
    psk_percent = decimal_field(data.get("psk_percent"), "psk_percent", nullable=True)
    term_months = _optional_int(data.get("term_months"), "term_months")

    if total_repayment is not None and total_repayment < credit_principal:
        raise ValueError("credit_total_repayment must be >= credit_principal")
    if monthly_payment is not None and term_months is None:
        raise ValueError("term_months is required when monthly_payment is set")
    if term_months is not None and monthly_payment is None:
        raise ValueError("monthly_payment is required when term_months is set")

    currency = str(data.get("currency") or "RUB").strip().upper()

    now = datetime.now(UTC).replace(tzinfo=None).isoformat()
    started_at = str(data.get("started_at") or now).strip() or now

    record = {
        "owner_id": owner_id,
        "printer_model": model,
        "equipment_price": money_float(equipment_price),
        "setup_cost": money_float(setup_cost),
        "down_payment": money_float(down_payment),
        "credit_principal": money_float(credit_principal),
        "credit_total_repayment": money_float(total_repayment)
        if total_repayment is not None
        else None,
        "monthly_payment": money_float(monthly_payment)
        if monthly_payment is not None
        else None,
        "term_months": term_months,
        "psk_percent": money_float(psk_percent) if psk_percent is not None else None,
        "currency": currency,
        "started_at": started_at,
        "updated_at": now,
    }
    with store.transaction() as tx:
        tx.set("printing/finance/" + owner_id, record)
    return get_financing_plan(store, owner_id) or {}


def get_financing_plan(store: PrintStore, owner_id: str) -> dict[str, Any] | None:
    current = store.get("printing/finance/" + owner_id)
    if current is not None:
        return current
    plans = list(store.items(prefix="printing/finance/").values())
    return max(plans, key=lambda row: row["updated_at"]) if plans else None


def _month_profit(
    store: PrintStore, owner_id: str, currency: str, month: str
) -> Decimal:
    from printing3d.service import list_orders

    profit = Decimal("0")
    for order in list_orders(store, owner_id, status="paid"):
        if str(order.get("currency") or "RUB") != currency:
            continue
        settled_at = str(order.get("settled_at") or "")
        if not settled_at.startswith(month):
            continue
        revenue = Decimal(str(order.get("actual_revenue") or 0))
        cost = Decimal(str(order.get("actual_cost") or 0))
        profit += revenue - cost
    return profit


def _cumulative_profit(store: PrintStore, owner_id: str, currency: str) -> Decimal:
    from printing3d.service import get_financial_summary

    summary = get_financial_summary(store, owner_id)
    for bucket in summary["currencies"]:
        if bucket["currency"] == currency:
            return Decimal(str(bucket["settled_profit"]))
    return Decimal("0")


def get_payback_status(
    store: PrintStore, owner_id: str, *, month: str | None = None
) -> dict[str, Any]:
    plan = get_financing_plan(store, owner_id)
    if plan is None:
        return {"plan": None, "status": "not_configured"}

    selected_month = month or datetime.now(UTC).strftime("%Y-%m")
    if not _MONTH_RE.fullmatch(selected_month):
        raise ValueError("month must use YYYY-MM format")

    equipment_price = Decimal(str(plan["equipment_price"]))
    setup_cost = Decimal(str(plan["setup_cost"]))
    credit_principal = Decimal(str(plan["credit_principal"]))
    total_repayment_raw = plan.get("credit_total_repayment")

    financing_cost_known = total_repayment_raw is not None or credit_principal == 0
    financing_cost = Decimal("0")
    if total_repayment_raw is not None:
        financing_cost = Decimal(str(total_repayment_raw)) - credit_principal

    known_cost_basis = equipment_price + setup_cost + financing_cost
    cumulative_profit = _cumulative_profit(store, owner_id, plan["currency"])
    month_profit = _month_profit(store, owner_id, plan["currency"], selected_month)
    remaining = max(known_cost_basis - cumulative_profit, Decimal("0"))

    progress = None
    if known_cost_basis > 0:
        progress = cumulative_profit / known_cost_basis * Decimal("100")

    monthly_payment_raw = plan.get("monthly_payment")
    payment_coverage_ratio = None
    payment_covered = None
    if monthly_payment_raw is not None and Decimal(str(monthly_payment_raw)) > 0:
        monthly_payment = Decimal(str(monthly_payment_raw))
        payment_coverage_ratio = month_profit / monthly_payment
        payment_covered = month_profit >= monthly_payment

    break_even_reached = None
    if financing_cost_known:
        break_even_reached = cumulative_profit >= known_cost_basis

    return {
        "plan": plan,
        "status": "configured",
        "month": selected_month,
        "cost_basis_status": "complete" if financing_cost_known else "partial",
        "known_cost_basis": money_float(known_cost_basis),
        "credit_overpayment": money_float(financing_cost)
        if financing_cost_known
        else None,
        "cumulative_realized_profit": money_float(cumulative_profit),
        "month_realized_profit": money_float(month_profit),
        "remaining_to_known_cost_basis": money_float(remaining),
        "payback_progress_percent": money_float(progress)
        if progress is not None
        else None,
        "monthly_payment_coverage_ratio": (
            money_float(payment_coverage_ratio)
            if payment_coverage_ratio is not None
            else None
        ),
        "monthly_payment_covered": payment_covered,
        "break_even_reached": break_even_reached,
    }


def assess_financing(
    store: PrintStore, owner_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    """Assess sparse candidate terms without persisting or choosing a lender."""
    if not isinstance(data, dict):
        raise ValueError("finance assessment must be an object")

    saved = get_financing_plan(store, owner_id) or {}
    purchase_mode = str(data.get("purchase_mode") or "").strip().lower()
    if not purchase_mode:
        if saved and saved.get("credit_principal") in (None, 0, 0.0):
            purchase_mode = "cash"
        else:
            purchase_mode = "credit"
    if purchase_mode not in {"credit", "cash"}:
        raise ValueError("purchase_mode must be credit or cash")

    def resolve(name: str, default=None):
        value = data.get(name)
        if value is not None:
            return value
        if saved.get(name) is not None:
            return saved.get(name)
        return default

    printer_model = resolve("printer_model")
    equipment_price = resolve("equipment_price")
    setup_cost = resolve("setup_cost", 0)
    down_payment = resolve("down_payment")
    credit_principal = resolve("credit_principal")
    # A saved total repayment belongs to the saved loan. It is reused only while the loan terms it was
    # computed from are unchanged; otherwise it is recomputed from the new terms.
    loan_terms_given = any(
        data.get(name) is not None
        for name in ("credit_principal", "monthly_payment", "term_months")
    )
    if data.get("credit_total_repayment") is not None:
        credit_total_repayment = data.get("credit_total_repayment")
    elif loan_terms_given:
        credit_total_repayment = None
    else:
        credit_total_repayment = saved.get("credit_total_repayment")
    monthly_payment = resolve("monthly_payment")
    term_months = resolve("term_months")
    psk_percent = resolve("psk_percent")
    currency = str(resolve("currency", "RUB") or "RUB").strip().upper()
    expected_monthly_profit = data.get("expected_monthly_profit")
    target_coverage = data.get("target_payment_coverage")
    if target_coverage is None:
        target_coverage = 2

    missing_fields: list[str] = []
    if equipment_price is None:
        missing_fields.append("equipment_price")

    if purchase_mode == "cash":
        credit_principal = 0
        credit_total_repayment = 0
        monthly_payment = 0
        term_months = None
        psk_percent = None
    else:
        for name, value in (
            ("credit_principal", credit_principal),
            ("monthly_payment", monthly_payment),
            ("term_months", term_months),
        ):
            if value is None:
                missing_fields.append(name)

    observed_month = datetime.now(UTC).strftime("%Y-%m")
    observed_monthly_profit = _month_profit(store, owner_id, currency, observed_month)
    cumulative_profit = _cumulative_profit(store, owner_id, currency)

    profit_source = "none"
    profit_basis: Decimal | None = None
    if expected_monthly_profit is not None:
        profit_basis = decimal_field(expected_monthly_profit, "expected_monthly_profit")
        profit_source = "provided_or_agent_estimate"
    elif observed_monthly_profit > 0:
        profit_basis = observed_monthly_profit
        profit_source = "realized_current_month"

    assumptions: list[str] = []
    if data.get("setup_cost") is None and saved.get("setup_cost") is None:
        assumptions.append("setup_cost=0")
    if data.get("down_payment") is None and saved.get("down_payment") is None:
        assumptions.append("down_payment=derived_from_purchase_terms")
    if data.get("target_payment_coverage") is None:
        assumptions.append("target_payment_coverage=2")

    resolved = {
        "printer_model": printer_model,
        "equipment_price": equipment_price,
        "setup_cost": setup_cost,
        "down_payment": down_payment,
        "credit_principal": credit_principal,
        "credit_total_repayment": credit_total_repayment,
        "monthly_payment": monthly_payment,
        "term_months": term_months,
        "psk_percent": psk_percent,
        "currency": currency,
    }

    result: dict[str, Any] = {
        "status": "needs_terms" if missing_fields else "ready",
        "purchase_mode": purchase_mode,
        "missing_fields": missing_fields,
        "resolved_terms": resolved,
        "assumptions": assumptions,
        "saved_plan_used": bool(saved),
        "observed_month": observed_month,
        "observed_monthly_profit": money_float(observed_monthly_profit),
        "cumulative_realized_profit": money_float(cumulative_profit),
        "profit_source": profit_source,
        "profit_basis": money_float(profit_basis) if profit_basis is not None else None,
        "analysis": None,
    }
    if missing_fields:
        return result

    equipment = decimal_field(equipment_price, "equipment_price")
    setup = decimal_field(setup_cost, "setup_cost")
    principal = decimal_field(credit_principal, "credit_principal")
    if purchase_mode == "cash":
        down = equipment
    elif down_payment is None:
        down = max(equipment - principal, Decimal("0"))
    else:
        down = decimal_field(down_payment, "down_payment")
    result["resolved_terms"]["down_payment"] = money_float(down)

    if psk_percent is not None:
        result["resolved_terms"]["psk_percent"] = money_float(
            decimal_field(psk_percent, "psk_percent")
        )

    coverage_target = decimal_field(target_coverage, "target_payment_coverage")
    if coverage_target < 1:
        raise ValueError("target_payment_coverage must be >= 1")

    payment = Decimal("0")
    months = None
    repayment = Decimal("0")
    financing_cost = Decimal("0")
    if purchase_mode == "credit":
        payment = decimal_field(monthly_payment, "monthly_payment")
        months = _optional_int(term_months, "term_months")
        if payment <= 0:
            raise ValueError("monthly_payment must be > 0")
        if principal > equipment:
            raise ValueError("credit_principal cannot exceed equipment_price")
        repayment = (
            decimal_field(credit_total_repayment, "credit_total_repayment")
            if credit_total_repayment is not None
            else payment * Decimal(months)
        )
        if repayment < principal:
            raise ValueError("credit_total_repayment must be >= credit_principal")
        financing_cost = repayment - principal

    known_cost_basis = equipment + setup + financing_cost
    upfront_cash = down + setup
    required_monthly_profit = payment * coverage_target

    coverage_ratio = None
    coverage_ok = None
    payback_months = None
    if profit_basis is not None and profit_basis > 0:
        payback_months = known_cost_basis / profit_basis
        if payment > 0:
            coverage_ratio = profit_basis / payment
            coverage_ok = profit_basis >= required_monthly_profit

    result["analysis"] = {
        "known_cost_basis": money_float(known_cost_basis),
        "upfront_cash_required": money_float(upfront_cash),
        "total_credit_repayment": money_float(repayment),
        "financing_cost": money_float(financing_cost),
        "target_payment_coverage": money_float(coverage_target),
        "required_monthly_profit": money_float(required_monthly_profit),
        "payment_coverage_ratio": money_float(coverage_ratio)
        if coverage_ratio is not None
        else None,
        "payment_coverage_ok": coverage_ok,
        "estimated_payback_months": money_float(payback_months)
        if payback_months is not None
        else None,
        "profit_evidence_status": "available"
        if profit_basis is not None
        else "not_available",
    }
    return result
