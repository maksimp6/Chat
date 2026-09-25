"""Government Department: deterministic case and IP-registration workflow."""
from __future__ import annotations

import json
import time
from typing import Any, Mapping, Optional
from uuid import uuid4

from flask import Blueprint, jsonify, request
from db import get_conn

government_bp = Blueprint("government", __name__, url_prefix="/api/government")

CASE_TYPES = ("IP_REGISTRATION", "TAX_RETURN", "GOV_REQUEST")
CASE_STATUSES = ("DRAFT", "PENDING_USER_DATA", "VALIDATING", "READY_FOR_APPROVAL", "SUBMITTED", "PROCESSING", "COMPLETED", "REJECTED", "ARCHIVED")
IP_REQUIRED_FIELDS = ("full_name", "birth_date", "citizenship", "passport")

def _now() -> int:
    return int(time.time())

def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)

def _parse(value: Any, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return default

def init_government_tables() -> None:
    conn = get_conn()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS government_cases (
            case_id TEXT PRIMARY KEY,
            case_type TEXT NOT NULL,
            status TEXT NOT NULL,
            user_id TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            documents_json TEXT NOT NULL DEFAULT '[]',
            deadlines_json TEXT NOT NULL DEFAULT '[]',
            responses_json TEXT NOT NULL DEFAULT '[]',
            user_approved INTEGER NOT NULL DEFAULT 0,
            approval_at INTEGER,
            submitted_at INTEGER,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_government_cases_user ON government_cases(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_government_cases_status ON government_cases(status)")
        conn.commit()
    finally:
        conn.close()

def _row(row: Any) -> dict[str, Any]:
    return {
        "case_id": row["case_id"], "case_type": row["case_type"], "status": row["status"],
        "user_id": row["user_id"], "payload": _parse(row["payload_json"], {}),
        "documents": _parse(row["documents_json"], []), "deadlines": _parse(row["deadlines_json"], []),
        "responses": _parse(row["responses_json"], []), "user_approved": bool(row["user_approved"]),
        "approval_at": row["approval_at"], "submitted_at": row["submitted_at"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }

def get_case(case_id: str) -> Optional[dict[str, Any]]:
    init_government_tables()
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM government_cases WHERE case_id = ?", (str(case_id),)).fetchone()
    finally:
        conn.close()
    return _row(row) if row else None

def _require_case(case_id: str) -> dict[str, Any]:
    case = get_case(case_id)
    if not case:
        raise ValueError("government case not found")
    return case

def _save(case: dict[str, Any]) -> dict[str, Any]:
    conn = get_conn()
    try:
        conn.execute("""UPDATE government_cases SET status=?, user_id=?, payload_json=?, documents_json=?,
            deadlines_json=?, responses_json=?, user_approved=?, approval_at=?, submitted_at=?, updated_at=?
            WHERE case_id=?""", (
            case["status"], case.get("user_id"), _json(case["payload"]), _json(case["documents"]),
            _json(case["deadlines"]), _json(case["responses"]), 1 if case.get("user_approved") else 0,
            case.get("approval_at"), case.get("submitted_at"), _now(), case["case_id"]))
        conn.commit()
    finally:
        conn.close()
    return _require_case(case["case_id"])

def create_case(case_type: str = "IP_REGISTRATION", user_id: Optional[str] = None) -> dict[str, Any]:
    case_type = str(case_type or "").strip().upper()
    if case_type not in CASE_TYPES:
        raise ValueError("unsupported government case type")
    init_government_tables()
    case_id = "gov-" + uuid4().hex[:16]
    now = _now()
    conn = get_conn()
    try:
        conn.execute("INSERT INTO government_cases (case_id,case_type,status,user_id,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                     (case_id, case_type, "DRAFT", user_id, now, now))
        conn.commit()
    finally:
        conn.close()
    return _require_case(case_id)

def requirements(case_type: str = "IP_REGISTRATION") -> dict[str, Any]:
    if str(case_type or "").strip().upper() != "IP_REGISTRATION":
        raise ValueError("requirements are not implemented for this case type")
    return {"case_type": "IP_REGISTRATION", "required_fields": list(IP_REQUIRED_FIELDS),
            "optional_fields": ["snils", "inn", "address", "okved", "tax_regime"],
            "document_templates": ["R21001"], "approval_required_before_submission": True}

def collect_data(case_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise ValueError("data must be an object")
    case = _require_case(case_id)
    payload = dict(case["payload"])
    payload.update(dict(data))
    case["payload"] = payload
    if case["status"] == "DRAFT":
        case["status"] = "PENDING_USER_DATA"
    return _save(case)

def validate_data(case_id: str) -> dict[str, Any]:
    case = _require_case(case_id)
    case["status"] = "VALIDATING"
    payload = case["payload"]
    errors = [{"field": field, "code": "required"} for field in IP_REQUIRED_FIELDS
              if not isinstance(payload.get(field), str) or not payload.get(field).strip()]
    okved = payload.get("okved")
    if okved is not None and (not isinstance(okved, list) or not all(isinstance(v, str) and v.strip() for v in okved)):
        errors.append({"field": "okved", "code": "invalid_format"})
    case["status"] = "PENDING_USER_DATA" if errors else "READY_FOR_APPROVAL"
    saved = _save(case)
    return {"case_id": saved["case_id"], "valid": not errors, "errors": errors,
            "status": saved["status"], "provided_fields": sorted(payload.keys())}

def tax_options() -> dict[str, Any]:
    return {"options": [{"id": "USN", "name": "УСН"}, {"id": "NPD", "name": "НПД"}, {"id": "OSNO", "name": "ОСНО"}],
            "note": "Eligibility and final tax choice require current authoritative rules and user confirmation."}

def prepare_application(case_id: str) -> dict[str, Any]:
    validation = validate_data(case_id)
    if not validation["valid"]:
        raise ValueError("case data is incomplete")
    case = _require_case(case_id)
    if case["case_type"] != "IP_REGISTRATION":
        raise ValueError("application preparation is only implemented for IP_REGISTRATION")
    document = {"document_id": "doc-" + uuid4().hex[:16], "template": "R21001",
                "format": "structured-json", "case_id": case_id, "status": "prepared"}
    case["documents"] = [d for d in case["documents"] if d.get("template") != "R21001"]
    case["documents"].append(document)
    return _save(case)

def prepare_documents(case_id: str) -> dict[str, Any]:
    prepare_application(case_id)
    case = _require_case(case_id)
    return {"case_id": case_id, "documents": case["documents"], "status": case["status"]}

def request_approval(case_id: str) -> dict[str, Any]:
    case = _require_case(case_id)
    if case["status"] != "READY_FOR_APPROVAL" or not case["documents"]:
        raise ValueError("case is not ready for approval")
    return {"case_id": case_id, "status": case["status"], "approval_required": True, "user_approved": False}

def approve_case(case_id: str) -> dict[str, Any]:
    case = _require_case(case_id)
    if case["status"] != "READY_FOR_APPROVAL":
        raise ValueError("case is not ready for approval")
    case["user_approved"] = True
    case["approval_at"] = _now()
    return _save(case)

def submit_case(case_id: str) -> dict[str, Any]:
    case = _require_case(case_id)
    if case["status"] != "READY_FOR_APPROVAL":
        raise ValueError("case is not ready for submission")
    if not case.get("user_approved"):
        raise PermissionError("explicit user approval is required before submission")
    case["status"] = "SUBMITTED"
    case["submitted_at"] = _now()
    return _save(case)

def status(case_id: str) -> dict[str, Any]:
    case = _require_case(case_id)
    return {"case_id": case_id, "status": case["status"], "user_approved": case["user_approved"],
            "documents": case["documents"], "deadlines": case["deadlines"], "responses": case["responses"]}

def process_response(case_id: str, response: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(response, Mapping):
        raise ValueError("response must be an object")
    case = _require_case(case_id)
    case["responses"].append({"response_id": "response-" + uuid4().hex[:12], "received_at": _now(), "data": dict(response)})
    state = str(response.get("status") or "").upper()
    if state in {"COMPLETED", "REJECTED", "PROCESSING"}:
        case["status"] = state
    return _save(case)

def archive_case(case_id: str) -> dict[str, Any]:
    case = _require_case(case_id)
    if case["status"] not in {"DRAFT", "PENDING_USER_DATA", "COMPLETED", "REJECTED", "ARCHIVED"}:
        raise ValueError("case cannot be archived in its current state")
    case["status"] = "ARCHIVED"
    return _save(case)

def _tool(fn, *, read_only: bool, requires_approval: bool, schema: dict, title: str, description: str) -> dict[str, Any]:
    return {"title": title, "description": description, "parameters": schema, "capabilities": ["government"],
            "risk_level": "low" if read_only else "high", "read_only": read_only,
            "requires_approval": requires_approval, "supported_transports": ["responses_api", "local_agent", "mcp"],
            "executor": {"type": "local"}, "func": fn}

def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}

_CASE = {"case_id": {"type": "string", "minLength": 1, "maxLength": 128}}
def _tool_create(args, cfg=None): return create_case(args.get("case_type", "IP_REGISTRATION"), args.get("user_id"))
def _tool_requirements(args, cfg=None): return requirements(args.get("case_type", "IP_REGISTRATION"))
def _tool_collect(args, cfg=None): return collect_data(args["case_id"], args.get("data") or {})
def _tool_validate(args, cfg=None): return validate_data(args["case_id"])
def _tool_tax_options(args, cfg=None): return tax_options()
def _tool_prepare_application(args, cfg=None): return prepare_application(args["case_id"])
def _tool_prepare_documents(args, cfg=None): return prepare_documents(args["case_id"])
def _tool_request_approval(args, cfg=None): return request_approval(args["case_id"])
def _tool_status(args, cfg=None): return status(args["case_id"])
def _tool_process_response(args, cfg=None): return process_response(args["case_id"], args.get("response") or {})
def _tool_archive(args, cfg=None): return archive_case(args["case_id"])
def _tool_submit(args, cfg=None): return submit_case(args["case_id"])

GOVERNMENT_TOOLS = {
    "gov.case.create": _tool(_tool_create, read_only=False, requires_approval=False, schema=_schema({"case_type": {"type": "string", "enum": list(CASE_TYPES)}, "user_id": {"type": ["string", "null"]}}, []), title="Government Case Create", description="Создать государственное дело."),
    "gov.requirements": _tool(_tool_requirements, read_only=True, requires_approval=False, schema=_schema({"case_type": {"type": "string", "enum": ["IP_REGISTRATION"]}}, []), title="Government Requirements", description="Получить контракт данных для открытия ИП."),
    "gov.collect_data": _tool(_tool_collect, read_only=False, requires_approval=True, schema=_schema({**_CASE, "data": {"type": "object"}}, ["case_id", "data"]), title="Government Collect Data", description="Сохранить данные пользователя в дело."),
    "gov.validate_data": _tool(_tool_validate, read_only=False, requires_approval=False, schema=_schema(_CASE, ["case_id"]), title="Government Validate Data", description="Детерминированно проверить данные дела."),
    "gov.tax_options": _tool(_tool_tax_options, read_only=True, requires_approval=False, schema=_schema({}, []), title="Government Tax Options", description="Вернуть варианты налогового режима."),
    "gov.prepare_application": _tool(_tool_prepare_application, read_only=False, requires_approval=False, schema=_schema(_CASE, ["case_id"]), title="Government Prepare Application", description="Подготовить заявление Р21001."),
    "gov.prepare_documents": _tool(_tool_prepare_documents, read_only=False, requires_approval=False, schema=_schema(_CASE, ["case_id"]), title="Government Prepare Documents", description="Подготовить документы без отправки."),
    "gov.request_approval": _tool(_tool_request_approval, read_only=True, requires_approval=False, schema=_schema(_CASE, ["case_id"]), title="Government Request Approval", description="Проверить готовность дела к подтверждению."),
    "gov.status": _tool(_tool_status, read_only=True, requires_approval=False, schema=_schema(_CASE, ["case_id"]), title="Government Case Status", description="Получить состояние дела."),
    "gov.process_response": _tool(_tool_process_response, read_only=False, requires_approval=False, schema=_schema({**_CASE, "response": {"type": "object"}}, ["case_id", "response"]), title="Government Process Response", description="Сохранить ответ ведомства."),
    "gov.archive_case": _tool(_tool_archive, read_only=False, requires_approval=True, schema=_schema(_CASE, ["case_id"]), title="Government Archive Case", description="Архивировать дело."),
    "gov.submit": _tool(_tool_submit, read_only=False, requires_approval=True, schema=_schema(_CASE, ["case_id"]), title="Government Submit", description="Отправить подтверждённое дело через GovGateway."),
}


def ensure_government_department() -> None:
    from departments import get_department, upsert_department
    if get_department("government"):
        return
    upsert_department({
        "id": "government", "name": "Government", "type": "government",
        "description": "Долгоживущие государственные дела и документы.", "agent_id": "government",
        "tools": sorted(GOVERNMENT_TOOLS), "capabilities": ["government", "documents", "workflows"],
        "policies": {"approval_required": True, "submission_requires_user_approval": True, "trace_pii_redaction": True},
        "metadata": {"system": True},
    })

@government_bp.get("/cases/<case_id>")
def api_case(case_id: str):
    case = get_case(case_id)
    return jsonify({"case": case}) if case else (jsonify({"error": "government_case_not_found"}), 404)

@government_bp.post("/cases")
def api_create_case():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify({"case": create_case(data.get("case_type", "IP_REGISTRATION"), data.get("user_id"))}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

@government_bp.post("/cases/<case_id>/approve")
def api_approve_case(case_id: str):
    try:
        return jsonify({"case": approve_case(case_id)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409

@government_bp.post("/cases/<case_id>/submit")
def api_submit_case(case_id: str):
    try:
        return jsonify({"case": submit_case(case_id)})
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 409

__all__ = ["GOVERNMENT_TOOLS", "archive_case", "approve_case", "collect_data", "create_case",
           "ensure_government_department", "get_case", "government_bp", "init_government_tables",
           "prepare_application", "prepare_documents", "process_response", "request_approval",
           "requirements", "status", "submit_case", "tax_options", "validate_data"]
