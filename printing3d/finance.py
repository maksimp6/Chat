"""Financing and payback tracking for the Alice Pro 3D-printing business."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from db import get_conn

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _decimal(value: Any, name: str, *, nullable: bool = False) -> Decimal | None:
    if value is None and nullable:
        return None
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if number < 0:
        raise ValueError(f"{name} must be >= 0")
    return number


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


def init_3d_finance_tables() -> None:
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS print_finance_plans (
            owner_id TEXT PRIMARY KEY,
            printer_model TEXT NOT NULL,
            equipment_price REAL NOT NULL,
            setup_cost REAL NOT NULL DEFAULT 0,
            down_payment REAL NOT NULL DEFAULT 0,
            credit_principal REAL NOT NULL DEFAULT 0,
            credit_total_repayment REAL,
            monthly_payment REAL,
            term_months INTEGER,
            psk_percent REAL,
            currency TEXT NOT NULL DEFAULT 'RUB',
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


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


def set_financing_plan(owner_id: str, data: dict[str, Any]) -> dict[str, Any]:
    if not owner_id:
        raise ValueError("owner identity is required")
    if not isinstance(data, dict):
        raise ValueError("finance plan must be an object")

    model = str(data.get("printer_model") or "").strip()
    if not model:
        raise ValueError("printer_model is required")

    equipment_price = _decimal(data.get("equipment_price"), "equipment_price")
    setup_cost = _decimal(data.get("setup_cost", 0), "setup_cost")
    down_payment = _decimal(data.get("down_payment", 0), "down_payment")
    credit_principal = _decimal(data.get("credit_principal", 0), "credit_principal")
    total_repayment = _decimal(
        data.get("credit_total_repayment"),
        "credit_total_repayment",
        nullable=True,
    )
    monthly_payment = _decimal(
        data.get("monthly_payment"),
        "monthly_payment",
        nullable=True,
    )
    psk_percent = _decimal(data.get("psk_percent"), "psk_percent", nullable=True)
    term_months = _optional_int(data.get("term_months"), "term_months")

    if total_repayment is not None and total_repayment < credit_principal:
        raise ValueError("credit_total_repayment must be >= credit_principal")
    if monthly_payment is not None and term_months is None:
        raise ValueError("term_months is required when monthly_payment is set")
    if term_months is not None and monthly_payment is None:
        raise ValueError("monthly_payment is required when term_months is set")

    currency = str(data.get("currency") or "RUB").strip().upper()

    now = datetime.utcnow().isoformat()
    started_at = str(data.get("started_at") or now).strip() or now

    init_3d_finance_tables()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO print_finance_plans(
            owner_id, printer_model, equipment_price, setup_cost, down_payment,
            credit_principal, credit_total_repayment, monthly_payment, term_months,
            psk_percent, currency, started_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(owner_id) DO UPDATE SET
            printer_model = excluded.printer_model,
            equipment_price = excluded.equipment_price,
            setup_cost = excluded.setup_cost,
            down_payment = excluded.down_payment,
            credit_principal = excluded.credit_principal,
            credit_total_repayment = excluded.credit_total_repayment,
            monthly_payment = excluded.monthly_payment,
            term_months = excluded.term_months,
            psk_percent = excluded.psk_percent,
            currency = excluded.currency,
            started_at = excluded.started_at,
            updated_at = excluded.updated_at
        """,
        (
            owner_id,
            model,
            _money(equipment_price),
            _money(setup_cost),
            _money(down_payment),
            _money(credit_principal),
            _money(total_repayment) if total_repayment is not None else None,
            _money(monthly_payment) if monthly_payment is not None else None,
            term_months,
            _money(psk_percent) if psk_percent is not None else None,
            currency,
            started_at,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return get_financing_plan(owner_id) or {}


def get_financing_plan(owner_id: str) -> dict[str, Any] | None:
    init_3d_finance_tables()
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM print_finance_plans WHERE owner_id = ?",
        (owner_id,),
    ).fetchone()
    conn.close()
    return _plan_row(row) if row else None


def _month_profit(owner_id: str, currency: str, month: str) -> Decimal:
    from .service import list_orders

    profit = Decimal("0")
    for order in list_orders(owner_id, status="paid"):
        if str(order.get("currency") or "RUB") != currency:
            continue
        settled_at = str(order.get("settled_at") or "")
        if not settled_at.startswith(month):
            continue
        revenue = Decimal(str(order.get("actual_revenue") or 0))
        cost = Decimal(str(order.get("actual_cost") or 0))
        profit += revenue - cost
    return profit


def _cumulative_profit(owner_id: str, currency: str) -> Decimal:
    from .service import get_financial_summary

    summary = get_financial_summary(owner_id)
    for bucket in summary["currencies"]:
        if bucket["currency"] == currency:
            return Decimal(str(bucket["settled_profit"]))
    return Decimal("0")


def get_payback_status(owner_id: str, *, month: str | None = None) -> dict[str, Any]:
    plan = get_financing_plan(owner_id)
    if plan is None:
        return {"plan": None, "status": "not_configured"}

    selected_month = month or datetime.utcnow().strftime("%Y-%m")
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
    cumulative_profit = _cumulative_profit(owner_id, plan["currency"])
    month_profit = _month_profit(owner_id, plan["currency"], selected_month)
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
        "known_cost_basis": _money(known_cost_basis),
        "credit_overpayment": _money(financing_cost) if financing_cost_known else None,
        "cumulative_realized_profit": _money(cumulative_profit),
        "month_realized_profit": _money(month_profit),
        "remaining_to_known_cost_basis": _money(remaining),
        "payback_progress_percent": _money(progress) if progress is not None else None,
        "monthly_payment_coverage_ratio": (
            _money(payment_coverage_ratio) if payment_coverage_ratio is not None else None
        ),
        "monthly_payment_covered": payment_covered,
        "break_even_reached": break_even_reached,
    }
