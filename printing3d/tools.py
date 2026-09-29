"""Universal local tools for the Alice Pro 3D-printing business domain."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from treasury_identity import get_current_owner_id

from .finance import get_payback_status, set_financing_plan
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


def finance_status(
    arguments: Mapping[str, Any],
    cfg: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Read financing and payback status for the current owner."""
    month = arguments.get("month")
    return get_payback_status(_trusted_owner(cfg), month=str(month) if month else None)


def finance_plan_set(
    arguments: Mapping[str, Any],
    cfg: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Store financing terms after approval at the universal tool boundary."""
    return set_financing_plan(_trusted_owner(cfg), dict(arguments))


def _tool_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


PRINTING3D_TOOLS = {
    "printing3d.finance.status": {
        "title": "3D Printing Finance Status",
        "description": "Получить условия финансирования, окупаемость и покрытие платежа прибылью.",
        "parameters": _tool_schema(
            {
                "month": {
                    "anyOf": [
                        {"type": "string", "pattern": "^\\d{4}-(0[1-9]|1[0-2])$"},
                        {"type": "null"},
                    ]
                }
            },
            [],
        ),
        "capabilities": ["3d", "treasury", "finance", "read"],
        "risk_level": "low",
        "read_only": True,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": finance_status,
    },
    "printing3d.finance.plan.set": {
        "title": "3D Printing Finance Plan Set",
        "description": "Сохранить стоимость принтера и условия кредита для расчёта окупаемости.",
        "parameters": _tool_schema(
            {
                "printer_model": {"type": "string", "minLength": 1, "maxLength": 200},
                "equipment_price": {"type": "number", "minimum": 0},
                "setup_cost": {"type": "number", "minimum": 0},
                "down_payment": {"type": "number", "minimum": 0},
                "credit_principal": {"type": "number", "minimum": 0},
                "credit_total_repayment": {
                    "anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]
                },
                "monthly_payment": {
                    "anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]
                },
                "term_months": {
                    "anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]
                },
                "psk_percent": {
                    "anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]
                },
                "currency": {"type": "string", "minLength": 1, "maxLength": 8},
                "started_at": {
                    "anyOf": [{"type": "string", "maxLength": 64}, {"type": "null"}]
                },
            },
            ["printer_model", "equipment_price", "setup_cost", "down_payment", "credit_principal", "currency"],
        ),
        "capabilities": ["3d", "treasury", "finance", "write"],
        "risk_level": "medium",
        "read_only": False,
        "requires_approval": True,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": finance_plan_set,
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
