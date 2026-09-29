"""Universal local tools for the Alice Pro 3D-printing business domain."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from treasury_identity import get_current_owner_id

from .finance import calculate_financing_plan
from .service import ORDER_STATUSES, get_financial_summary, list_orders


def _trusted_owner(cfg: Optional[dict[str, Any]] = None) -> str:
    context = (cfg or {}).get("_universal_context") if isinstance(cfg, dict) else None
    call = context.get("call") if isinstance(context, dict) else None
    user_id = getattr(call, "user_id", None) if call is not None else None
    if user_id and str(user_id).strip():
        return str(user_id).strip()
    return get_current_owner_id(required=True)


def treasury_summary(
    _arguments: Mapping[str, Any],
    cfg: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Read the current owner's realized 3D-printing P&L."""
    return get_financial_summary(_trusted_owner(cfg))


def orders_list(
    arguments: Mapping[str, Any],
    cfg: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Read the current owner's 3D-printing order queue."""
    owner_id = _trusted_owner(cfg)
    raw_status = arguments.get("status")
    status = str(raw_status).strip() if raw_status else None
    return {"orders": list_orders(owner_id, status=status)}


def finance_calculate(
    arguments: Mapping[str, Any],
    _cfg: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Calculate credit burden and payback from explicit user-supplied terms."""
    return calculate_financing_plan(arguments)


def _tool_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


PRINTING3D_TOOLS = {
    "printing3d.finance.calculate": {
        "title": "3D Printing Financing Calculator",
        "description": (
            "Рассчитать нагрузку кредита и окупаемость 3D-принтера по явным условиям "
            "покупки без выбора банка или кредитора."
        ),
        "parameters": _tool_schema(
            {
                "equipment_price": {"type": "number", "minimum": 0},
                "startup_costs": {"type": "number", "minimum": 0},
                "financed_principal": {"type": "number", "minimum": 0},
                "monthly_payment": {"type": "number", "minimum": 0},
                "term_months": {"type": "integer", "minimum": 0},
                "planned_monthly_profit": {"type": "number", "minimum": 0},
                "target_payment_coverage": {
                    "anyOf": [
                        {"type": "number", "minimum": 1},
                        {"type": "null"},
                    ]
                },
                "psk_percent": {
                    "anyOf": [
                        {"type": "number", "minimum": 0},
                        {"type": "null"},
                    ]
                },
                "currency": {
                    "anyOf": [
                        {"type": "string", "minLength": 1, "maxLength": 8},
                        {"type": "null"},
                    ]
                },
            },
            [
                "equipment_price",
                "startup_costs",
                "financed_principal",
                "monthly_payment",
                "term_months",
                "planned_monthly_profit",
            ],
        ),
        "capabilities": ["3d", "treasury", "finance", "read"],
        "risk_level": "low",
        "read_only": True,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": finance_calculate,
    },
    "printing3d.treasury.summary": {
        "title": "3D Printing Treasury Summary",
        "description": (
            "Получить фактическую выручку, расходы и прибыль 3D-печати для текущего пользователя."
        ),
        "parameters": _tool_schema({}, []),
        "capabilities": ["3d", "treasury", "read"],
        "risk_level": "low",
        "read_only": True,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": treasury_summary,
    },
    "printing3d.orders.list": {
        "title": "3D Printing Orders",
        "description": "Получить очередь заказов 3D-печати текущего пользователя.",
        "parameters": _tool_schema(
            {
                "status": {
                    "anyOf": [
                        {"type": "string", "enum": list(ORDER_STATUSES)},
                        {"type": "null"},
                    ]
                }
            },
            [],
        ),
        "capabilities": ["3d", "orders", "read"],
        "risk_level": "low",
        "read_only": True,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": orders_list,
    },
}
