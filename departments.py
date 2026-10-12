"""First-class Department registry and API for Alice Pro."""

from __future__ import annotations
import json
import os
import secrets
import time
from typing import Any, Mapping, Optional
from uuid import uuid4
from flask import Blueprint, jsonify, request
from db import get_store
from session_manager import create_session, get_session

departments_bp = Blueprint("departments", __name__, url_prefix="/api/departments")
_DEPT_ID_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789-_"


def _now() -> int:
    return int(time.time())


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _parse(value: Any, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def init_department_tables() -> None:
    get_store()


def _admin_ok() -> bool:
    expected = os.getenv("ALICE_DEPARTMENTS_ADMIN_TOKEN", "").strip()
    supplied = request.headers.get("X-Department-Admin-Token", "").strip()
    return bool(expected and supplied and secrets.compare_digest(expected, supplied))


def _validate_id(value: str) -> str:
    value = str(value or "").strip().lower()
    if not value or len(value) > 64 or any(ch not in _DEPT_ID_CHARS for ch in value):
        raise ValueError("invalid department id")
    return value


def list_departments() -> list[dict[str, Any]]:
    return sorted(
        get_store().items(prefix="department/").values(),
        key=lambda item: (item["name"], item["id"]),
    )


def get_department(department_id: str) -> Optional[dict[str, Any]]:
    return get_store().get("department/" + _validate_id(department_id))


def upsert_department(
    data: Mapping[str, Any], department_id: Optional[str] = None
) -> dict[str, Any]:
    ident = _validate_id(department_id or data.get("id") or ("dept-" + uuid4().hex[:12]))
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("department name is required")
    record: dict[str, Any] = {
        "id": ident,
        "name": name,
        "type": str(data.get("type") or "general"),
        "description": str(data.get("description") or ""),
        "agent_id": str(data.get("agent_id") or "").strip() or None,
        "system_prompt": str(data.get("system_prompt") or ""),
        "status": str(data.get("status") or "active"),
        "updated_at": _now(),
    }
    for field in ("model_config", "policies", "knowledge_scope", "session_config", "metadata"):
        record[field] = dict(data.get(field) or {})
    for field in ("tools", "capabilities"):
        record[field] = list(data.get(field) or [])
    with get_store().transaction() as tx:
        old = tx.get("department/" + ident)
        record["created_at"] = old["created_at"] if old else record["updated_at"]
        tx.set("department/" + ident, record)
    return record


@departments_bp.get("")
def departments_list():
    return jsonify({"departments": list_departments()})


@departments_bp.post("")
def departments_create():
    if not _admin_ok():
        return jsonify({"error": "department admin authorization required"}), 401
    try:
        return jsonify({"department": upsert_department(request.get_json(silent=True) or {})}), 201
    except ValueError:
        return jsonify({"error": "invalid_department_request"}), 400


@departments_bp.get("/<department_id>")
def departments_get(department_id: str):
    try:
        department = get_department(department_id)
    except ValueError:
        return jsonify({"error": "invalid_department_id"}), 400
    if not department:
        return jsonify({"error": "department_not_found"}), 404
    return jsonify({"department": department})


@departments_bp.put("/<department_id>")
def departments_update(department_id: str):
    if not _admin_ok():
        return jsonify({"error": "department admin authorization required"}), 401
    try:
        return jsonify(
            {"department": upsert_department(request.get_json(silent=True) or {}, department_id)}
        )
    except ValueError:
        return jsonify({"error": "invalid_department_request"}), 400


@departments_bp.delete("/<department_id>")
def departments_delete(department_id: str):
    if not _admin_ok():
        return jsonify({"error": "department admin authorization required"}), 401
    department = get_department(department_id)
    if not department:
        return jsonify({"error": "department_not_found"}), 404
    key = "department/" + _validate_id(department_id)
    with get_store().transaction() as tx:
        record = tx.get(key)
        if record is not None:
            tx.set(key, {**record, "status": "archived", "updated_at": _now()})
    return jsonify({"status": "archived", "department_id": department_id})


@departments_bp.post("/<department_id>/sessions")
def departments_start_session(department_id: str):
    department = get_department(department_id)
    if not department:
        return jsonify({"error": "department_not_found"}), 404
    if department["status"] != "active":
        return jsonify({"error": "department_not_active"}), 409
    session_id = str(uuid4())
    create_session(
        session_id,
        {
            "department_id": department_id,
            "department_type": department["type"],
            "agent_id": department["agent_id"],
        },
    )
    session = get_session(session_id)
    return jsonify({"session": session, "department": department}), 201


@departments_bp.get("/<department_id>/sessions/<session_id>")
def departments_session_status(department_id: str, session_id: str):
    department = get_department(department_id)
    if not department:
        return jsonify({"error": "department_not_found"}), 404
    session = get_session(session_id)
    if not session or (session.get("metadata") or {}).get("department_id") != department_id:
        return jsonify({"error": "session_not_found"}), 404
    return jsonify({"session": session, "department_id": department_id})
