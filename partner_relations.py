"""Partner Relations Department: trusted-owner CRM, communications and follow-up tools.

The module keeps partner data scoped to the authenticated Alice Pro identity.
External delivery is intentionally separated from preparation/history storage:
the current implementation can prepare and queue approved outgoing messages,
but it does not pretend to have an external transport when none is configured.
"""

from __future__ import annotations

import json
import time
from typing import Any, Mapping, Optional
from uuid import uuid4

from flask import Blueprint, jsonify, request

from db import get_store
from economy.treasury_identity import TreasuryIdentityError, get_current_owner_id


partner_relations_bp = Blueprint(
    "partner_relations",
    __name__,
    url_prefix="/api/partners",
)


PARTNER_STATUSES = (
    "lead",
    "active",
    "negotiating",
    "paused",
    "completed",
    "archived",
)
MESSAGE_DIRECTIONS = ("incoming", "outgoing")
MESSAGE_STATUSES = ("prepared", "queued", "recorded")
FOLLOWUP_STATUSES = ("open", "completed", "cancelled")


def _now() -> int:
    return int(time.time())


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _parse(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return parsed


def _trusted_owner(cfg: Optional[dict[str, Any]] = None) -> str:
    context = (cfg or {}).get("_universal_context") if isinstance(cfg, dict) else None
    call = context.get("call") if isinstance(context, dict) else None
    user_id = getattr(call, "user_id", None) if call is not None else None
    if user_id and str(user_id).strip():
        return str(user_id).strip()
    return get_current_owner_id(required=True)


def _trace_event(cfg: Optional[dict[str, Any]], event_type: str, payload: dict[str, Any]) -> None:
    context = (cfg or {}).get("_universal_context") if isinstance(cfg, dict) else None
    call = context.get("call") if isinstance(context, dict) else None
    metadata = getattr(call, "metadata", {}) if call is not None else {}
    trace = metadata.get("execution_trace") if isinstance(metadata, dict) else None
    if trace is not None and hasattr(trace, "add_event"):
        trace.add_event(event_type, payload)


def init_partner_relations_tables() -> None:
    get_store()


def _validate_status(value: str, allowed: tuple[str, ...], field: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in allowed:
        raise ValueError(f"invalid {field}")
    return normalized


def _partner_row(row: Any) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "owner_id"}


def _contact_row(row: Any) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "owner_id"}


def _message_row(row: Any) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "owner_id"}


def _followup_row(row: Any) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "owner_id"}


def _get_partner(owner_id: str, partner_id: str) -> Optional[dict[str, Any]]:
    return _get_owned("partner", str(partner_id), owner_id)


def create_partner(
    arguments: Mapping[str, Any], cfg: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    owner_id = _trusted_owner(cfg)
    name = str(arguments.get("name") or "").strip()
    if not name:
        raise ValueError("partner name is required")
    status = _validate_status(arguments.get("status") or "lead", PARTNER_STATUSES, "partner status")
    partner_id = str(arguments.get("id") or uuid4())
    now = _now()

    init_partner_relations_tables()
    _insert_owned(
        "partner",
        partner_id,
        {
            "id": partner_id,
            "owner_id": owner_id,
            "name": name,
            "organization": str(arguments.get("organization") or "").strip(),
            "email": str(arguments.get("email") or "").strip(),
            "phone": str(arguments.get("phone") or "").strip(),
            "status": status,
            "notes": str(arguments.get("notes") or "").strip(),
            "metadata": dict(arguments.get("metadata") or {}),
            "created_at": now,
            "updated_at": now,
        },
    )

    _trace_event(
        cfg,
        "partner_action",
        {
            "action": "create",
            "partner_id": partner_id,
            "status": status,
            "owner_id": owner_id,
        },
    )
    return _get_partner(owner_id, partner_id) or {}


def list_partners(
    arguments: Mapping[str, Any], cfg: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    owner_id = _trusted_owner(cfg)
    status = str(arguments.get("status") or "").strip().lower()
    init_partner_relations_tables()
    if status:
        _validate_status(status, PARTNER_STATUSES, "partner status")
    rows = [
        row for row in _owned_rows("partner", owner_id) if not status or row["status"] == status
    ]
    rows.sort(key=lambda row: (-row["updated_at"], row["name"]))
    return {"partners": [_partner_row(row) for row in rows]}


def get_partner(
    arguments: Mapping[str, Any], cfg: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    owner_id = _trusted_owner(cfg)
    partner_id = str(arguments.get("partner_id") or arguments.get("id") or "").strip()
    if not partner_id:
        raise ValueError("partner_id is required")
    partner = _get_partner(owner_id, partner_id)
    if not partner:
        raise ValueError("partner not found")
    init_partner_relations_tables()
    contacts = _owned_rows("contact", owner_id, partner_id)
    contacts.sort(key=lambda row: (-row["updated_at"], row["name"]))
    messages = sorted(
        _owned_rows("message", owner_id, partner_id), key=lambda row: row["created_at"]
    )
    followups = sorted(
        _owned_rows("followup", owner_id, partner_id),
        key=lambda row: (row["due_at"] is None, row["due_at"] or 0, row["created_at"]),
    )

    partner["contacts"] = [_contact_row(row) for row in contacts]
    partner["messages"] = [_message_row(row) for row in messages]
    partner["followups"] = [_followup_row(row) for row in followups]
    return {"partner": partner}


def delete_partner(
    arguments: Mapping[str, Any], cfg: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    owner_id = _trusted_owner(cfg)
    partner_id = str(arguments.get("partner_id") or "").strip()
    if not partner_id:
        raise ValueError("partner_id is required")
    if not _get_partner(owner_id, partner_id):
        raise ValueError("partner not found")
    now = _now()
    _update_owned("partner", partner_id, owner_id, {"status": "archived", "updated_at": now})
    _trace_event(
        cfg,
        "partner_action",
        {
            "action": "delete",
            "partner_id": partner_id,
            "owner_id": owner_id,
            "result": "archived",
        },
    )
    return {"deleted": True, "partner_id": partner_id, "status": "archived"}


def update_partner(
    arguments: Mapping[str, Any], cfg: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    owner_id = _trusted_owner(cfg)
    partner_id = str(arguments.get("partner_id") or "").strip()
    if not partner_id:
        raise ValueError("partner_id is required")
    if not _get_partner(owner_id, partner_id):
        raise ValueError("partner not found")

    allowed = {
        "name": str(arguments.get("name") or "").strip(),
        "organization": str(arguments.get("organization") or "").strip(),
        "email": str(arguments.get("email") or "").strip(),
        "phone": str(arguments.get("phone") or "").strip(),
        "notes": str(arguments.get("notes") or "").strip(),
    }
    status = _validate_status(arguments.get("status") or "lead", PARTNER_STATUSES, "partner status")
    now = _now()
    _update_owned("partner", partner_id, owner_id, {**allowed, "status": status, "updated_at": now})
    _trace_event(
        cfg,
        "partner_action",
        {
            "action": "update",
            "partner_id": partner_id,
            "status": status,
            "owner_id": owner_id,
        },
    )
    return get_partner({"partner_id": partner_id}, cfg)


def add_contact(
    arguments: Mapping[str, Any], cfg: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    owner_id = _trusted_owner(cfg)
    partner_id = str(arguments.get("partner_id") or "").strip()
    name = str(arguments.get("name") or "").strip()
    if not partner_id or not name:
        raise ValueError("partner_id and contact name are required")
    if not _get_partner(owner_id, partner_id):
        raise ValueError("partner not found")
    contact_id = str(arguments.get("id") or uuid4())
    now = _now()
    _insert_owned(
        "contact",
        contact_id,
        {
            "id": contact_id,
            "owner_id": owner_id,
            "partner_id": partner_id,
            "name": name,
            "role": str(arguments.get("role") or "").strip(),
            "email": str(arguments.get("email") or "").strip(),
            "phone": str(arguments.get("phone") or "").strip(),
            "notes": str(arguments.get("notes") or "").strip(),
            "created_at": now,
            "updated_at": now,
        },
    )
    _trace_event(
        cfg,
        "partner_action",
        {
            "action": "contact_create",
            "partner_id": partner_id,
            "contact_id": contact_id,
            "owner_id": owner_id,
        },
    )
    return {"contact": _contact_for_owner(owner_id, contact_id)}


def _contact_for_owner(owner_id: str, contact_id: str) -> Optional[dict[str, Any]]:
    return _get_owned("contact", contact_id, owner_id)


def list_contacts(
    arguments: Mapping[str, Any], cfg: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    owner_id = _trusted_owner(cfg)
    partner_id = str(arguments.get("partner_id") or "").strip()
    if not partner_id:
        raise ValueError("partner_id is required")
    if not _get_partner(owner_id, partner_id):
        raise ValueError("partner not found")
    rows = sorted(
        _owned_rows("contact", owner_id, partner_id),
        key=lambda row: (-row["updated_at"], row["name"]),
    )
    return {"contacts": [_contact_row(row) for row in rows]}


def update_contact(
    arguments: Mapping[str, Any], cfg: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    owner_id = _trusted_owner(cfg)
    contact_id = str(arguments.get("contact_id") or "").strip()
    if not contact_id:
        raise ValueError("contact_id is required")
    existing = _contact_for_owner(owner_id, contact_id)
    if not existing:
        raise ValueError("contact not found")
    now = _now()
    changes = {
        field: str(arguments.get(field) or existing[field]).strip()
        for field in ("name", "role", "email", "phone", "notes")
    }
    _update_owned("contact", contact_id, owner_id, {**changes, "updated_at": now})
    _trace_event(
        cfg,
        "partner_action",
        {
            "action": "contact_update",
            "partner_id": existing["partner_id"],
            "contact_id": contact_id,
            "owner_id": owner_id,
        },
    )
    return {"contact": _contact_for_owner(owner_id, contact_id)}


def delete_contact(
    arguments: Mapping[str, Any], cfg: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    owner_id = _trusted_owner(cfg)
    contact_id = str(arguments.get("contact_id") or "").strip()
    if not contact_id:
        raise ValueError("contact_id is required")
    existing = _contact_for_owner(owner_id, contact_id)
    if not existing:
        raise ValueError("contact not found")
    key = "partners/contact/" + contact_id
    with get_store().transaction() as tx:
        row = tx.get(key)
        if row is None or row["owner_id"] != owner_id:
            raise ValueError("contact not found")
        tx.delete(key)
    _trace_event(
        cfg,
        "partner_action",
        {
            "action": "contact_delete",
            "partner_id": existing["partner_id"],
            "contact_id": contact_id,
            "owner_id": owner_id,
        },
    )
    return {"deleted": True, "contact_id": contact_id, "partner_id": existing["partner_id"]}


def prepare_message(
    arguments: Mapping[str, Any], cfg: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    owner_id = _trusted_owner(cfg)
    partner_id = str(arguments.get("partner_id") or "").strip()
    body = str(arguments.get("body") or "")
    if not partner_id or not body.strip():
        raise ValueError("partner_id and message body are required")
    if not _get_partner(owner_id, partner_id):
        raise ValueError("partner not found")
    direction = _validate_status(
        arguments.get("direction") or "outgoing", MESSAGE_DIRECTIONS, "message direction"
    )
    message_id = str(arguments.get("message_id") or uuid4())
    status = "prepared" if direction == "outgoing" else "recorded"
    now = _now()
    _insert_owned(
        "message",
        message_id,
        {
            "id": message_id,
            "owner_id": owner_id,
            "partner_id": partner_id,
            "direction": direction,
            "subject": str(arguments.get("subject") or "").strip(),
            "body": body,
            "status": status,
            "reference_type": str(arguments.get("reference_type") or "").strip(),
            "reference_id": str(arguments.get("reference_id") or "").strip(),
            "external_ref": "",
            "created_at": now,
            "updated_at": now,
        },
    )
    _trace_event(
        cfg,
        "partner_message_prepared",
        {
            "action": "message_prepare",
            "partner_id": partner_id,
            "message_id": message_id,
            "direction": direction,
            "status": status,
            "owner_id": owner_id,
        },
    )
    return {"message": get_message(owner_id, message_id), "delivery": {"status": "prepared"}}


def get_message(owner_id: str, message_id: str) -> Optional[dict[str, Any]]:
    return _get_owned("message", message_id, owner_id)


def queue_message(
    arguments: Mapping[str, Any], cfg: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    owner_id = _trusted_owner(cfg)
    message_id = str(arguments.get("message_id") or "").strip()
    if not message_id:
        raise ValueError("message_id is required")
    message = get_message(owner_id, message_id)
    if not message:
        raise ValueError("message not found")
    if message["direction"] != "outgoing":
        raise ValueError("only outgoing messages can be queued")
    now = _now()
    changed = _update_owned(
        "message",
        message_id,
        owner_id,
        {"status": "queued", "updated_at": now},
        allowed_statuses=("prepared", "queued"),
    )
    _trace_event(
        cfg,
        "partner_message_queued",
        {
            "action": "message_send",
            "partner_id": message["partner_id"],
            "message_id": message_id,
            "status": "queued" if changed else message["status"],
            "owner_id": owner_id,
            "transport": "not_configured",
        },
    )
    return {
        "message": get_message(owner_id, message_id),
        "delivery": {
            "status": "queued" if changed else message["status"],
            "transport": "not_configured",
            "note": "Внешний канал доставки не настроен; сообщение сохранено в истории и ожидает transport adapter.",
        },
    }


def get_thread(
    arguments: Mapping[str, Any], cfg: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    owner_id = _trusted_owner(cfg)
    partner_id = str(arguments.get("partner_id") or "").strip()
    if not partner_id:
        raise ValueError("partner_id is required")
    if not _get_partner(owner_id, partner_id):
        raise ValueError("partner not found")
    rows = sorted(_owned_rows("message", owner_id, partner_id), key=lambda row: row["created_at"])
    return {"messages": [_message_row(row) for row in rows]}


def update_status(
    arguments: Mapping[str, Any], cfg: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    owner_id = _trusted_owner(cfg)
    partner_id = str(arguments.get("partner_id") or "").strip()
    status = _validate_status(arguments.get("status"), PARTNER_STATUSES, "partner status")
    if not partner_id or not _get_partner(owner_id, partner_id):
        raise ValueError("partner not found")
    _update_owned("partner", partner_id, owner_id, {"status": status, "updated_at": _now()})
    _trace_event(
        cfg,
        "partner_status_updated",
        {
            "action": "status_update",
            "partner_id": partner_id,
            "status": status,
            "owner_id": owner_id,
        },
    )
    return _get_partner(owner_id, partner_id) or {}


def create_followup(
    arguments: Mapping[str, Any], cfg: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    owner_id = _trusted_owner(cfg)
    partner_id = str(arguments.get("partner_id") or "").strip()
    title = str(arguments.get("title") or "").strip()
    if not partner_id or not title:
        raise ValueError("partner_id and follow-up title are required")
    if not _get_partner(owner_id, partner_id):
        raise ValueError("partner not found")
    followup_id = str(arguments.get("id") or uuid4())
    due_at = arguments.get("due_at")
    due_value = int(due_at) if due_at not in (None, "") else None
    now = _now()
    _insert_owned(
        "followup",
        followup_id,
        {
            "id": followup_id,
            "owner_id": owner_id,
            "partner_id": partner_id,
            "title": title,
            "due_at": due_value,
            "status": "open",
            "notes": str(arguments.get("notes") or "").strip(),
            "reference_type": str(arguments.get("reference_type") or "").strip(),
            "reference_id": str(arguments.get("reference_id") or "").strip(),
            "created_at": now,
            "updated_at": now,
        },
    )
    _trace_event(
        cfg,
        "partner_followup_created",
        {
            "action": "followup_create",
            "partner_id": partner_id,
            "followup_id": followup_id,
            "owner_id": owner_id,
        },
    )
    return get_followup(owner_id, followup_id) or {}


def get_followup(owner_id: str, followup_id: str) -> Optional[dict[str, Any]]:
    return _get_owned("followup", followup_id, owner_id)


def complete_followup(
    arguments: Mapping[str, Any], cfg: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    owner_id = _trusted_owner(cfg)
    followup_id = str(arguments.get("followup_id") or "").strip()
    if not followup_id:
        raise ValueError("followup_id is required")
    existing = get_followup(owner_id, followup_id)
    if not existing:
        raise ValueError("followup not found")
    status = _validate_status(
        arguments.get("status") or "completed", FOLLOWUP_STATUSES, "follow-up status"
    )
    now = _now()
    _update_owned("followup", followup_id, owner_id, {"status": status, "updated_at": now})
    _trace_event(
        cfg,
        "partner_followup_completed",
        {
            "action": "followup_complete",
            "partner_id": existing["partner_id"],
            "followup_id": followup_id,
            "status": status,
            "owner_id": owner_id,
        },
    )
    return get_followup(owner_id, followup_id) or {}


def _tool_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


PARTNER_TOOLS = {
    "partner.create": {
        "title": "Partner Create",
        "description": "Создать партнёра в личном реестре текущего пользователя.",
        "parameters": _tool_schema(
            {
                "name": {"type": "string", "minLength": 1, "maxLength": 200},
                "organization": {"type": "string", "maxLength": 200},
                "email": {"type": "string", "maxLength": 320},
                "phone": {"type": "string", "maxLength": 64},
                "status": {"type": "string", "enum": list(PARTNER_STATUSES)},
                "notes": {"type": "string", "maxLength": 10000},
                "metadata": {"type": "object"},
                "id": {"anyOf": [{"type": "string", "maxLength": 128}, {"type": "null"}]},
            },
            ["name", "organization", "email", "phone", "status", "notes", "metadata", "id"],
        ),
        "capabilities": ["partner", "crm"],
        "risk_level": "medium",
        "read_only": False,
        "requires_approval": True,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": create_partner,
    },
    "partner.list": {
        "title": "Partner List",
        "description": "Список партнёров текущего пользователя.",
        "parameters": _tool_schema(
            {
                "status": {
                    "anyOf": [{"type": "string", "enum": list(PARTNER_STATUSES)}, {"type": "null"}]
                },
            },
            ["status"],
        ),
        "capabilities": ["partner", "crm", "read"],
        "risk_level": "low",
        "read_only": True,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": list_partners,
    },
    "partner.get": {
        "title": "Partner Get",
        "description": "Получить партнёра, контакты, переписку и follow-up только в собственной области доступа.",
        "parameters": _tool_schema(
            {
                "partner_id": {"type": "string", "minLength": 1, "maxLength": 128},
            },
            ["partner_id"],
        ),
        "capabilities": ["partner", "crm", "read"],
        "risk_level": "low",
        "read_only": True,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": get_partner,
    },
    "partner.update": {
        "title": "Partner Update",
        "description": "Изменить партнёра в личном реестре текущего пользователя.",
        "parameters": _tool_schema(
            {
                "partner_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "name": {"type": "string", "minLength": 1, "maxLength": 200},
                "organization": {"type": "string", "maxLength": 200},
                "email": {"type": "string", "maxLength": 320},
                "phone": {"type": "string", "maxLength": 64},
                "status": {"type": "string", "enum": list(PARTNER_STATUSES)},
                "notes": {"type": "string", "maxLength": 10000},
            },
            ["partner_id", "name", "organization", "email", "phone", "status", "notes"],
        ),
        "capabilities": ["partner", "crm"],
        "risk_level": "medium",
        "read_only": False,
        "requires_approval": True,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": update_partner,
    },
    "partner.contact.create": {
        "title": "Partner Contact Create",
        "description": "Добавить контакт партнёра.",
        "parameters": _tool_schema(
            {
                "partner_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "name": {"type": "string", "minLength": 1, "maxLength": 200},
                "role": {"type": "string", "maxLength": 200},
                "email": {"type": "string", "maxLength": 320},
                "phone": {"type": "string", "maxLength": 64},
                "notes": {"type": "string", "maxLength": 10000},
                "id": {"anyOf": [{"type": "string", "maxLength": 128}, {"type": "null"}]},
            },
            ["partner_id", "name", "role", "email", "phone", "notes", "id"],
        ),
        "capabilities": ["partner", "crm"],
        "risk_level": "medium",
        "read_only": False,
        "requires_approval": True,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": add_contact,
    },
    "partner.delete": {
        "title": "Partner Archive",
        "description": "Архивировать партнёра, сохранив историю коммуникаций.",
        "parameters": _tool_schema(
            {
                "partner_id": {"type": "string", "minLength": 1, "maxLength": 128},
            },
            ["partner_id"],
        ),
        "capabilities": ["partner", "crm", "delete"],
        "risk_level": "high",
        "read_only": False,
        "requires_approval": True,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": delete_partner,
    },
    "partner.contact.list": {
        "title": "Partner Contact List",
        "description": "Получить контакты выбранного партнёра.",
        "parameters": _tool_schema(
            {
                "partner_id": {"type": "string", "minLength": 1, "maxLength": 128},
            },
            ["partner_id"],
        ),
        "capabilities": ["partner", "contact", "read"],
        "risk_level": "low",
        "read_only": True,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": list_contacts,
    },
    "partner.contact.update": {
        "title": "Partner Contact Update",
        "description": "Изменить контакт партнёра.",
        "parameters": _tool_schema(
            {
                "contact_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "name": {"type": "string", "minLength": 1, "maxLength": 200},
                "role": {"type": "string", "maxLength": 200},
                "email": {"type": "string", "maxLength": 320},
                "phone": {"type": "string", "maxLength": 64},
                "notes": {"type": "string", "maxLength": 10000},
            },
            ["contact_id", "name", "role", "email", "phone", "notes"],
        ),
        "capabilities": ["partner", "contact"],
        "risk_level": "medium",
        "read_only": False,
        "requires_approval": True,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": update_contact,
    },
    "partner.contact.delete": {
        "title": "Partner Contact Delete",
        "description": "Удалить контакт партнёра из личного реестра.",
        "parameters": _tool_schema(
            {
                "contact_id": {"type": "string", "minLength": 1, "maxLength": 128},
            },
            ["contact_id"],
        ),
        "capabilities": ["partner", "contact", "delete"],
        "risk_level": "high",
        "read_only": False,
        "requires_approval": True,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": delete_contact,
    },
    "partner.message.prepare": {
        "title": "Partner Message Prepare",
        "description": "Подготовить исходящее сообщение партнёру и сохранить его в истории без внешней отправки.",
        "parameters": _tool_schema(
            {
                "partner_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "subject": {"type": "string", "maxLength": 300},
                "body": {"type": "string", "minLength": 1, "maxLength": 50000},
                "direction": {"type": "string", "enum": list(MESSAGE_DIRECTIONS)},
                "reference_type": {"type": "string", "maxLength": 100},
                "reference_id": {"type": "string", "maxLength": 200},
                "message_id": {"anyOf": [{"type": "string", "maxLength": 128}, {"type": "null"}]},
            },
            [
                "partner_id",
                "subject",
                "body",
                "direction",
                "reference_type",
                "reference_id",
                "message_id",
            ],
        ),
        "capabilities": ["partner", "communication"],
        "risk_level": "medium",
        "read_only": False,
        "requires_approval": True,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": prepare_message,
    },
    "partner.message.send": {
        "title": "Partner Message Send",
        "description": "Поставить ранее подготовленное исходящее сообщение в очередь после обязательного подтверждения; внешний transport adapter должен быть настроен отдельно.",
        "parameters": _tool_schema(
            {
                "message_id": {"type": "string", "minLength": 1, "maxLength": 128},
            },
            ["message_id"],
        ),
        "capabilities": ["partner", "communication", "external_action"],
        "risk_level": "high",
        "read_only": False,
        "requires_approval": True,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": queue_message,
    },
    "partner.thread.get": {
        "title": "Partner Thread Get",
        "description": "Получить историю переписки с партнёром.",
        "parameters": _tool_schema(
            {
                "partner_id": {"type": "string", "minLength": 1, "maxLength": 128},
            },
            ["partner_id"],
        ),
        "capabilities": ["partner", "communication", "read"],
        "risk_level": "low",
        "read_only": True,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": get_thread,
    },
    "partner.status.update": {
        "title": "Partner Status Update",
        "description": "Обновить статус отношений с партнёром.",
        "parameters": _tool_schema(
            {
                "partner_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "status": {"type": "string", "enum": list(PARTNER_STATUSES)},
            },
            ["partner_id", "status"],
        ),
        "capabilities": ["partner", "crm"],
        "risk_level": "medium",
        "read_only": False,
        "requires_approval": True,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": update_status,
    },
    "partner.followup.create": {
        "title": "Partner Follow-up Create",
        "description": "Создать follow-up для партнёра.",
        "parameters": _tool_schema(
            {
                "partner_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "title": {"type": "string", "minLength": 1, "maxLength": 300},
                "due_at": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                "notes": {"type": "string", "maxLength": 10000},
                "reference_type": {"type": "string", "maxLength": 100},
                "reference_id": {"type": "string", "maxLength": 200},
                "id": {"anyOf": [{"type": "string", "maxLength": 128}, {"type": "null"}]},
            },
            ["partner_id", "title", "due_at", "notes", "reference_type", "reference_id", "id"],
        ),
        "capabilities": ["partner", "followup"],
        "risk_level": "medium",
        "read_only": False,
        "requires_approval": True,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": create_followup,
    },
    "partner.followup.complete": {
        "title": "Partner Follow-up Complete",
        "description": "Завершить или отменить follow-up партнёра.",
        "parameters": _tool_schema(
            {
                "followup_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "status": {"type": "string", "enum": list(FOLLOWUP_STATUSES)},
            },
            ["followup_id", "status"],
        ),
        "capabilities": ["partner", "followup"],
        "risk_level": "medium",
        "read_only": False,
        "requires_approval": True,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": complete_followup,
    },
}


def _http_owner() -> str:
    try:
        return get_current_owner_id()
    except TreasuryIdentityError as exc:
        raise exc


@partner_relations_bp.get("")
def partners_list():
    try:
        return jsonify(list_partners({}))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 401


@partner_relations_bp.post("")
def partners_create():
    try:
        return jsonify({"partner": create_partner(request.get_json(silent=True) or {})}), 201
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@partner_relations_bp.get("/<partner_id>")
def partners_get(partner_id: str):
    try:
        result = get_partner({"partner_id": partner_id})
        return jsonify(result)
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except ValueError as exc:
        status = 404 if str(exc) == "partner not found" else 400
        return jsonify({"error": str(exc)}), status


@partner_relations_bp.put("/<partner_id>")
def partners_update(partner_id: str):
    data = request.get_json(silent=True) or {}
    data["partner_id"] = partner_id
    try:
        return jsonify(update_partner(data))
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except ValueError as exc:
        status = 404 if str(exc) == "partner not found" else 400
        return jsonify({"error": str(exc)}), status


@partner_relations_bp.delete("/<partner_id>")
def partners_delete(partner_id: str):
    try:
        return jsonify(delete_partner({"partner_id": partner_id}))
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except ValueError as exc:
        status = 404 if str(exc) == "partner not found" else 400
        return jsonify({"error": str(exc)}), status


@partner_relations_bp.get("/<partner_id>/contacts")
def partner_contacts_list(partner_id: str):
    try:
        return jsonify(list_contacts({"partner_id": partner_id}))
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except ValueError as exc:
        status = 404 if str(exc) == "partner not found" else 400
        return jsonify({"error": str(exc)}), status


@partner_relations_bp.post("/<partner_id>/contacts")
def partner_contact_create(partner_id: str):
    data = request.get_json(silent=True) or {}
    data["partner_id"] = partner_id
    try:
        return jsonify(add_contact(data)), 201
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except ValueError as exc:
        status = 404 if str(exc) == "partner not found" else 400
        return jsonify({"error": str(exc)}), status


@partner_relations_bp.put("/contacts/<contact_id>")
def partner_contact_update(contact_id: str):
    data = request.get_json(silent=True) or {}
    data["contact_id"] = contact_id
    try:
        return jsonify(update_contact(data))
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except ValueError as exc:
        status = 404 if str(exc) == "contact not found" else 400
        return jsonify({"error": str(exc)}), status


@partner_relations_bp.delete("/contacts/<contact_id>")
def partner_contact_delete(contact_id: str):
    try:
        return jsonify(delete_contact({"contact_id": contact_id}))
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except ValueError as exc:
        status = 404 if str(exc) == "contact not found" else 400
        return jsonify({"error": str(exc)}), status


@partner_relations_bp.get("/<partner_id>/messages")
def partner_messages(partner_id: str):
    try:
        return jsonify(get_thread({"partner_id": partner_id}))
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except ValueError as exc:
        status = 404 if str(exc) == "partner not found" else 400
        return jsonify({"error": str(exc)}), status


@partner_relations_bp.post("/<partner_id>/messages")
def partner_message_record(partner_id: str):
    data = request.get_json(silent=True) or {}
    data["partner_id"] = partner_id
    try:
        result = prepare_message(data)
        return jsonify(result), 201
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except ValueError as exc:
        status = 404 if str(exc) == "partner not found" else 400
        return jsonify({"error": str(exc)}), status


@partner_relations_bp.post("/<partner_id>/followups")
def partner_followup_create(partner_id: str):
    data = request.get_json(silent=True) or {}
    data["partner_id"] = partner_id
    try:
        return jsonify({"followup": create_followup(data)}), 201
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except ValueError as exc:
        status = 404 if str(exc) == "partner not found" else 400
        return jsonify({"error": str(exc)}), status


@partner_relations_bp.post("/<partner_id>/status")
def partner_status_update(partner_id: str):
    data = request.get_json(silent=True) or {}
    data["partner_id"] = partner_id
    try:
        return jsonify({"partner": update_status(data)})
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except ValueError as exc:
        status = 404 if str(exc) == "partner not found" else 400
        return jsonify({"error": str(exc)}), status


@partner_relations_bp.post("/followups/<followup_id>/complete")
def partner_followup_complete(followup_id: str):
    data = request.get_json(silent=True) or {}
    data["followup_id"] = followup_id
    try:
        return jsonify({"followup": complete_followup(data)})
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except ValueError as exc:
        status = 404 if str(exc) == "followup not found" else 400
        return jsonify({"error": str(exc)}), status


def ensure_partner_department() -> None:
    from departments import get_department, upsert_department

    if get_department("partner-relations"):
        return
    upsert_department(
        {
            "id": "partner-relations",
            "name": "Partner Relations",
            "type": "partner-relations",
            "description": "Поиск, ведение и сопровождение коммуникации с партнёрами.",
            "agent_id": "partner-relations",
            "capabilities": ["partners", "contacts", "communication", "followup", "orders"],
            "tools": sorted(PARTNER_TOOLS),
            "policies": {
                "external_messages_require_approval": True,
                "trusted_owner_required": True,
            },
            "knowledge_scope": {
                "owner_scoped": True,
                "reference_fields": ["reference_type", "reference_id"],
            },
            "session_config": {
                "tool_category": "partner",
            },
            "metadata": {"system": True},
        }
    )


__all__ = [
    "PARTNER_TOOLS",
    "ensure_partner_department",
    "init_partner_relations_tables",
    "partner_relations_bp",
]


def _get_owned(kind: str, identity: str, owner_id: str) -> Optional[dict[str, Any]]:
    row = get_store().get("partners/" + kind + "/" + identity)
    return (
        {key: value for key, value in row.items() if key != "owner_id"}
        if row and row["owner_id"] == owner_id
        else None
    )


def _owned_rows(kind: str, owner_id: str, partner_id: Optional[str] = None) -> list[dict[str, Any]]:
    return [
        row
        for row in get_store().items(prefix="partners/" + kind + "/").values()
        if row["owner_id"] == owner_id and (partner_id is None or row["partner_id"] == partner_id)
    ]


def _insert_owned(kind: str, identity: str, row: dict[str, Any]) -> None:
    key = "partners/" + kind + "/" + identity
    with get_store().transaction() as tx:
        if tx.get(key) is not None:
            raise ValueError("record already exists")
        if kind != "partner":
            parent = tx.get("partners/partner/" + row["partner_id"])
            if parent is None or parent["owner_id"] != row["owner_id"]:
                raise ValueError("partner not found")
        tx.set(key, row)


def _update_owned(
    kind: str,
    identity: str,
    owner_id: str,
    changes: dict[str, Any],
    *,
    allowed_statuses: tuple[str, ...] = (),
) -> bool:
    key = "partners/" + kind + "/" + identity
    with get_store().transaction() as tx:
        row = tx.get(key)
        if row is None or row["owner_id"] != owner_id:
            raise ValueError("record not found")
        if allowed_statuses and row["status"] not in allowed_statuses:
            return False
        tx.set(key, {**row, **changes})
    return True
