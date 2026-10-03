"""Universal local tools for the Alice Pro 3D-printing business domain."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from treasury_identity import get_current_owner_id

from .finance import assess_financing, get_payback_status, set_financing_plan
from .service import (
    ORDER_STATUSES,
    calculate_quote,
    create_order,
    get_financial_summary,
    list_orders,
)

QUOTE_REQUIRED_FIELDS = ("material_grams", "material_cost_per_kg", "print_hours")
QUOTE_OPTIONAL_FIELDS = (
    "printer_power_watts",
    "electricity_cost_per_kwh",
    "depreciation_per_hour",
    "packaging_cost",
    "failure_rate_percent",
    "platform_fee_percent",
    "target_margin_percent",
)
ORDER_CREATE_STATUSES = ("lead", "quote", "accepted")


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


def finance_assess(
    arguments: Mapping[str, Any],
    cfg: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Assess sparse AI-collected financing terms without persisting them."""
    return assess_financing(_trusted_owner(cfg), dict(arguments))


def _quote_input(arguments: Mapping[str, Any]) -> dict[str, Any]:
    fields = QUOTE_REQUIRED_FIELDS + QUOTE_OPTIONAL_FIELDS + ("currency",)
    return {key: arguments[key] for key in fields if arguments.get(key) is not None}


def quote(
    arguments: Mapping[str, Any],
    cfg: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Calculate an order quote without persisting it."""
    data = _quote_input(arguments)
    # A missing cost driver would silently price the order at zero.
    missing = [key for key in QUOTE_REQUIRED_FIELDS if key not in data]
    if missing:
        return {"status": "needs_input", "missing_fields": missing}
    return {
        "status": "ok",
        "quote": calculate_quote(data),
        "assumed_defaults": [key for key in QUOTE_OPTIONAL_FIELDS if key not in data],
    }


def order_create(
    arguments: Mapping[str, Any],
    cfg: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Persist an accepted order after approval at the universal tool boundary."""
    status = str(arguments.get("status") or "quote").strip()
    if status not in ORDER_CREATE_STATUSES:
        raise ValueError(f"status must be one of {', '.join(ORDER_CREATE_STATUSES)}")
    data: dict[str, Any] = {
        key: arguments[key]
        for key in ("title", "customer_name", "quoted_price", "currency", "notes")
        if arguments.get(key) is not None
    }
    data["status"] = status
    data["source"] = "alice"
    quote_input = _quote_input(arguments.get("quote") or {})
    if quote_input:
        missing = [key for key in QUOTE_REQUIRED_FIELDS if key not in quote_input]
        if missing:
            raise ValueError(f"quote is missing: {', '.join(missing)}")
        data["quote"] = quote_input
    return {"order": create_order(_trusted_owner(cfg), data)}


def _nullable_number(minimum: float = 0) -> dict[str, Any]:
    return {"anyOf": [{"type": "number", "minimum": minimum}, {"type": "null"}]}


_QUOTE_PROPERTIES: dict[str, Any] = {
    "material_grams": _nullable_number(),
    "material_cost_per_kg": _nullable_number(),
    "print_hours": _nullable_number(),
    "printer_power_watts": _nullable_number(),
    "electricity_cost_per_kwh": _nullable_number(),
    "depreciation_per_hour": _nullable_number(),
    "packaging_cost": _nullable_number(),
    "failure_rate_percent": {
        "anyOf": [{"type": "number", "minimum": 0, "exclusiveMaximum": 100}, {"type": "null"}]
    },
    "platform_fee_percent": _nullable_number(),
    "target_margin_percent": _nullable_number(),
    "currency": {"anyOf": [{"type": "string", "minLength": 1, "maxLength": 8}, {"type": "null"}]},
}


def _tool_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


PRINTING3D_TOOLS = {
    "printing3d.quote": {
        "title": "3D Printing Order Quote",
        "description": (
            "Рассчитать себестоимость и рекомендуемую цену заказа 3D-печати: материал, "
            "электричество, амортизация, упаковка, резерв на брак, комиссия площадки и "
            "целевая маржа. Ничего не сохраняет. Вес модели, цену материала за кг и время "
            "печати бери из диалога, слайсера или поиска; неизвестные значения передавай "
            "как null — инструмент вернёт missing_fields. Остальные параметры имеют "
            "консервативные значения по умолчанию и перечисляются в assumed_defaults."
        ),
        "parameters": _tool_schema(_QUOTE_PROPERTIES, []),
        "capabilities": ["3d", "orders", "read"],
        "risk_level": "low",
        "read_only": True,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": quote,
    },
    "printing3d.order.create": {
        "title": "3D Printing Order Create",
        "description": (
            "Сохранить заказ 3D-печати после явного согласия пользователя. Передай исходные "
            "данные расчёта в quote — цена будет пересчитана на сервере; quoted_price "
            "указывай, только если пользователь согласовал другую цену."
        ),
        "parameters": _tool_schema(
            {
                "title": {"type": "string", "minLength": 1, "maxLength": 200},
                "customer_name": {
                    "anyOf": [{"type": "string", "maxLength": 200}, {"type": "null"}]
                },
                "status": {
                    "anyOf": [
                        {"type": "string", "enum": list(ORDER_CREATE_STATUSES)},
                        {"type": "null"},
                    ]
                },
                "quote": {"anyOf": [_tool_schema(_QUOTE_PROPERTIES, []), {"type": "null"}]},
                "quoted_price": _nullable_number(),
                "currency": _QUOTE_PROPERTIES["currency"],
                "notes": {"anyOf": [{"type": "string", "maxLength": 2000}, {"type": "null"}]},
            },
            ["title"],
        ),
        "capabilities": ["3d", "orders", "write"],
        "risk_level": "medium",
        "read_only": False,
        "requires_approval": True,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": order_create,
    },
    "printing3d.finance.assess": {
        "title": "3D Printing Finance Assess",
        "description": (
            "AI-first read-only оценка покупки 3D-принтера. Сначала используй уже известные "
            "данные из диалога, поиска и подключённых финансовых инструментов. Не проси "
            "пользователя повторно вводить известные значения; неизвестные поля передавай "
            "как null. Инструмент объединяет найденные условия с уже сохранённым планом, "
            "учитывает фактическую прибыль 3D-направления и возвращает missing_fields."
        ),
        "parameters": _tool_schema(
            {
                "purchase_mode": {
                    "anyOf": [
                        {"type": "string", "enum": ["credit", "cash"]},
                        {"type": "null"},
                    ]
                },
                "printer_model": {
                    "anyOf": [
                        {"type": "string", "minLength": 1, "maxLength": 200},
                        {"type": "null"},
                    ]
                },
                "equipment_price": {"anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]},
                "setup_cost": {"anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]},
                "down_payment": {"anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]},
                "credit_principal": {"anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]},
                "credit_total_repayment": {
                    "anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]
                },
                "monthly_payment": {"anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]},
                "term_months": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
                "psk_percent": {"anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]},
                "currency": {
                    "anyOf": [
                        {"type": "string", "minLength": 1, "maxLength": 8},
                        {"type": "null"},
                    ]
                },
                "expected_monthly_profit": {
                    "anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]
                },
                "target_payment_coverage": {
                    "anyOf": [{"type": "number", "minimum": 1}, {"type": "null"}]
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
                "monthly_payment": {"anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]},
                "term_months": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
                "psk_percent": {"anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]},
                "currency": {"type": "string", "minLength": 1, "maxLength": 8},
                "started_at": {"anyOf": [{"type": "string", "maxLength": 64}, {"type": "null"}]},
            },
            [
                "printer_model",
                "equipment_price",
                "setup_cost",
                "down_payment",
                "credit_principal",
                "currency",
            ],
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
