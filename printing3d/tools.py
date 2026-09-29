"""Universal local tools for the Alice Pro 3D-printing business domain."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from treasury_identity import get_current_owner_id

from .finance import assess_financing_plan
from .service import ORDER_STATUSES, get_financial_summary, get_recent_profit, list_orders


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


def finance_assess(
    arguments: Mapping[str, Any],
    cfg: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Assess sparse AI-collected credit terms using realized 3D profit when needed."""
    owner_id = _trusted_owner(cfg)
    currency = str(arguments.get("currency") or "RUB")
    observed_profit = get_recent_profit(owner_id, currency=currency, days=30)
    return assess_financing_plan(arguments, observed_monthly_profit=observed_profit)


def _tool_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


PRINTING3D_TOOLS = {
    "printing3d.finance.assess": {
        "title": "3D Printing Financing Assessment",
        "description": (
            "AI-first оценка кредита и окупаемости 3D-принтера. Перед вызовом модель "
            "использует уже известные условия из диалога и доступных инструментов, "
            "не просит пользователя повторно вводить известные цифры и передаёт null "
            "для неизвестных значений. Инструмент сам учитывает фактическую прибыль "
            "3D-направления за последние 30 дней и возвращает missing_fields, если "
            "каких-то условий кредита ещё не хватает."
        ),
        "parameters": _tool_schema(
            {
                "purchase_mode": {
                    "anyOf": [
                        {"type": "string", "enum": ["credit", "cash"]},
                        {"type": "null"},
                    ]
                },
                "equipment_price": {
                    "anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]
                },
                "startup_costs": {
                    "anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]
                },
                "financed_principal": {
                    "anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]
                },
                "monthly_payment": {
                    "anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]
                },
                "term_months": {
                    "anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]
                },
                "planned_monthly_profit": {
                    "anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]
                },
                "target_payment_coverage": {
                    "anyOf": [{"type": "number", "minimum": 1}, {"type": "null"}]
                },
                "psk_percent": {
                    "anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]
                },
                "currency": {
                    "anyOf": [
                        {"type": "string", "minLength": 1, "maxLength": 8},
                        {"type": "null"},
                    ]
                },
            },
            [],
        ),
        "capabilities": ["3d", "treasury", "finance", "read"],
        "risk_level": "low",
        "read_only": True,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": finance_assess,
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
