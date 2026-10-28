"""Universal local tools for the Logos 3D-printing business domain."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Mapping
from typing import Any

from forge._vendor.schema import object_schema
from forge.binding import printing_store as _store
from forge.host import get_current_owner_id
from printing3d import finance, service
from printing3d.plate import MAX_FILE_BYTES, inspect_archive

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


def _trusted_owner(cfg: dict[str, Any] | None = None) -> str:
    return get_current_owner_id()


PRINT_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.(?:gcode\.)?3mf$")


def plate_inspect(
    arguments: Mapping[str, Any],
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read one owner's staged 3MF without arbitrary filesystem access."""
    name = arguments.get("artifact_name")
    if type(name) is not str or not PRINT_ARTIFACT_NAME.fullmatch(name):
        raise ValueError("artifact_name must be one plain .3mf or .gcode.3mf filename")
    root = os.environ.get("LOGOS_3D_ARTIFACT_ROOT")
    if not root:
        return {
            "status": "needs_configuration",
            "missing_fields": ["LOGOS_3D_ARTIFACT_ROOT"],
        }
    # Use the installation's shared artifact namespace.
    owner = _trusted_owner(cfg)
    owner_folder = hashlib.sha256(owner.encode("utf-8")).hexdigest()
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        root_fd = os.open(root, flags | os.O_DIRECTORY)
        try:
            owner_fd = os.open(owner_folder, flags | os.O_DIRECTORY, dir_fd=root_fd)
            try:
                file_fd = os.open(name, flags | os.O_NONBLOCK, dir_fd=owner_fd)
                with os.fdopen(file_fd, "rb") as handle:
                    info = os.fstat(handle.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                        return {
                            "status": "blocked",
                            "reason": "not a bounded regular file",
                        }
                    return inspect_archive(handle, name)
            finally:
                os.close(owner_fd)
        finally:
            os.close(root_fd)
    except FileNotFoundError:
        return {"status": "not_found", "artifact_name": name}
    except OSError:
        # Don't expose operator filesystem paths in model-facing diagnostics.
        return {"status": "blocked", "reason": "artifact cannot be opened safely"}


def get_treasury_summary(
    _arguments: Mapping[str, Any],
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read the current owner's realized 3D-printing P&L."""
    return service.get_financial_summary(_store(), _trusted_owner(cfg))


def list_orders(
    arguments: Mapping[str, Any],
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read the current owner's 3D-printing order queue."""
    owner_id = _trusted_owner(cfg)
    raw_status = arguments.get("status")
    status = str(raw_status).strip() if raw_status else None
    return {"orders": service.list_orders(_store(), owner_id, status=status)}


def get_finance_status(
    arguments: Mapping[str, Any],
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read financing and payback status for the current owner."""
    month = arguments.get("month")
    return finance.get_payback_status(
        _store(), _trusted_owner(cfg), month=str(month) if month else None
    )


def set_financing_plan(
    arguments: Mapping[str, Any],
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store financing terms after approval at the universal tool boundary."""
    return finance.set_financing_plan(_store(), _trusted_owner(cfg), dict(arguments))


def assess_financing(
    arguments: Mapping[str, Any],
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assess sparse AI-collected financing terms without persisting them."""
    return finance.assess_financing(_store(), _trusted_owner(cfg), dict(arguments))


def _quote_input(arguments: Mapping[str, Any]) -> dict[str, Any]:
    fields = QUOTE_REQUIRED_FIELDS + QUOTE_OPTIONAL_FIELDS + ("currency",)
    return {key: arguments[key] for key in fields if arguments.get(key) is not None}


def quote(
    arguments: Mapping[str, Any],
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculate an order quote without persisting it."""
    data = _quote_input(arguments)
    # A missing cost driver would silently price the order at zero.
    missing = [key for key in QUOTE_REQUIRED_FIELDS if key not in data]
    if missing:
        return {"status": "needs_input", "missing_fields": missing}
    return {
        "status": "ok",
        "quote": service.calculate_quote(data),
        "assumed_defaults": [key for key in QUOTE_OPTIONAL_FIELDS if key not in data],
    }


def create_order_tool(
    arguments: Mapping[str, Any],
    cfg: dict[str, Any] | None = None,
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
    data["source"] = "host"
    raw_quote = arguments.get("quote")
    if raw_quote is not None:
        if not isinstance(raw_quote, Mapping):
            raise ValueError("quote must be an object")
        quote_input = _quote_input(raw_quote)
        missing = [key for key in QUOTE_REQUIRED_FIELDS if key not in quote_input]
        if missing:
            raise ValueError(f"quote is missing: {', '.join(missing)}")
        order_currency = data.get("currency")
        quote_currency = quote_input.get("currency")
        if order_currency and quote_currency and order_currency != quote_currency:
            raise ValueError("order currency and quote currency differ")
        currency = order_currency or quote_currency
        if currency:
            data["currency"] = quote_input["currency"] = currency
        data["quote"] = quote_input
    return {"order": service.create_order(_store(), _trusted_owner(cfg), data)}


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
        "anyOf": [
            {"type": "number", "minimum": 0, "exclusiveMaximum": 100},
            {"type": "null"},
        ]
    },
    "platform_fee_percent": _nullable_number(),
    "target_margin_percent": _nullable_number(),
    "currency": {
        "anyOf": [{"type": "string", "minLength": 1, "maxLength": 8}, {"type": "null"}]
    },
}


PRINTING3D_TOOLS = {
    "printing3d.plate.inspect": {
        "title": "3D Plate Preflight",
        "description": (
            "Read-only verify one owner-staged Bambu A1 mini .gcode.3mf file: "
            "ZIP integrity, material/profile, G-code heat commands and single plate. "
            "Return readiness findings; NEVER start/upload a print or read other paths."
        ),
        "parameters": object_schema(
            {"artifact_name": {"type": "string", "minLength": 5, "maxLength": 140}},
            ["artifact_name"],
        ),
        "capabilities": ["3d", "read", "preflight"],
        "risk_level": "low",
        "read_only": True,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": plate_inspect,
    },
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
        "parameters": object_schema(_QUOTE_PROPERTIES, []),
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
        "parameters": object_schema(
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
                "quote": {
                    "anyOf": [object_schema(_QUOTE_PROPERTIES, []), {"type": "null"}]
                },
                "quoted_price": _nullable_number(),
                "currency": _QUOTE_PROPERTIES["currency"],
                "notes": {
                    "anyOf": [{"type": "string", "maxLength": 2000}, {"type": "null"}]
                },
            },
            ["title"],
        ),
        "capabilities": ["3d", "orders", "write"],
        "risk_level": "medium",
        "read_only": False,
        "requires_approval": True,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": create_order_tool,
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
        "parameters": object_schema(
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
                "equipment_price": {
                    "anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]
                },
                "setup_cost": {
                    "anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]
                },
                "down_payment": {
                    "anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]
                },
                "credit_principal": {
                    "anyOf": [{"type": "number", "minimum": 0}, {"type": "null"}]
                },
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
        "func": assess_financing,
    },
    "printing3d.finance.status": {
        "title": "3D Printing Finance Status",
        "description": "Получить условия финансирования, окупаемость и покрытие платежа прибылью.",
        "parameters": object_schema(
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
        "func": get_finance_status,
    },
    "printing3d.finance.plan.set": {
        "title": "3D Printing Finance Plan Set",
        "description": "Сохранить стоимость принтера и условия кредита для расчёта окупаемости.",
        "parameters": object_schema(
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
        "func": set_financing_plan,
    },
    "printing3d.treasury.summary": {
        "title": "3D Printing Treasury Summary",
        "description": (
            "Получить фактическую выручку, расходы и прибыль 3D-печати для текущего пользователя."
        ),
        "parameters": object_schema({}, []),
        "capabilities": ["3d", "treasury", "read"],
        "risk_level": "low",
        "read_only": True,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": get_treasury_summary,
    },
    "printing3d.orders.list": {
        "title": "3D Printing Orders",
        "description": "Получить очередь заказов 3D-печати текущего пользователя.",
        "parameters": object_schema(
            {
                "status": {
                    "anyOf": [
                        {"type": "string", "enum": list(service.ORDER_STATUSES)},
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
        "func": list_orders,
    },
}
