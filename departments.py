"""First-class Department registry and API for Alice Pro."""

from __future__ import annotations
import json
import os
import secrets
import time
from typing import Any, Mapping, Optional
from uuid import uuid4
from flask import Blueprint, jsonify, request
from db import get_conn
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
    conn = get_conn()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS departments (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            agent_id TEXT,
            model_config_json TEXT NOT NULL DEFAULT '{}',
            system_prompt TEXT NOT NULL DEFAULT '',
            tools_json TEXT NOT NULL DEFAULT '[]',
            capabilities_json TEXT NOT NULL DEFAULT '[]',
            policies_json TEXT NOT NULL DEFAULT '{}',
            knowledge_scope_json TEXT NOT NULL DEFAULT '{}',
            session_config_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'active',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_departments_status ON departments(status)")
        conn.commit()
    finally:
        conn.close()


def _admin_ok() -> bool:
    expected = os.getenv("ALICE_DEPARTMENTS_ADMIN_TOKEN", "").strip()
    supplied = request.headers.get("X-Department-Admin-Token", "").strip()
    return bool(expected and supplied and secrets.compare_digest(expected, supplied))


def _validate_id(value: str) -> str:
    value = str(value or "").strip().lower()
    if not value or len(value) > 64 or any(ch not in _DEPT_ID_CHARS for ch in value):
        raise ValueError("invalid department id")
    return value


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "description": row["description"],
        "agent_id": row["agent_id"],
        "model_config": _parse(row["model_config_json"], {}),
        "system_prompt": row["system_prompt"],
        "tools": _parse(row["tools_json"], []),
        "capabilities": _parse(row["capabilities_json"], []),
        "policies": _parse(row["policies_json"], {}),
        "knowledge_scope": _parse(row["knowledge_scope_json"], {}),
        "session_config": _parse(row["session_config_json"], {}),
        "status": row["status"],
        "metadata": _parse(row["metadata_json"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_departments() -> list[dict[str, Any]]:
    init_department_tables()
    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM departments ORDER BY name ASC, id ASC").fetchall()
    finally:
        conn.close()
    return [_row_to_dict(row) for row in rows]


def get_department(department_id: str) -> Optional[dict[str, Any]]:
    department_id = _validate_id(department_id)
    init_department_tables()
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM departments WHERE id = ?", (department_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row else None


def upsert_department(
    data: Mapping[str, Any], department_id: Optional[str] = None
) -> dict[str, Any]:
    init_department_tables()
    ident = _validate_id(department_id or data.get("id") or ("dept-" + uuid4().hex[:12]))
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("department name is required")
    now = _now()
    payload = {
        "description": str(data.get("description") or ""),
        "agent_id": str(data.get("agent_id") or "").strip() or None,
        "model_config": dict(data.get("model_config") or {}),
        "system_prompt": str(data.get("system_prompt") or ""),
        "tools": list(data.get("tools") or []),
        "capabilities": list(data.get("capabilities") or []),
        "policies": dict(data.get("policies") or {}),
        "knowledge_scope": dict(data.get("knowledge_scope") or {}),
        "session_config": dict(data.get("session_config") or {}),
        "status": str(data.get("status") or "active"),
        "metadata": dict(data.get("metadata") or {}),
    }
    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT created_at FROM departments WHERE id = ?", (ident,)
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        conn.execute(
            """INSERT INTO departments
            (id,name,type,description,agent_id,model_config_json,system_prompt,tools_json,
             capabilities_json,policies_json,knowledge_scope_json,session_config_json,status,
             metadata_json,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name,type=excluded.type,description=excluded.description,
              agent_id=excluded.agent_id,model_config_json=excluded.model_config_json,
              system_prompt=excluded.system_prompt,tools_json=excluded.tools_json,
              capabilities_json=excluded.capabilities_json,policies_json=excluded.policies_json,
              knowledge_scope_json=excluded.knowledge_scope_json,session_config_json=excluded.session_config_json,
              status=excluded.status,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
            (
                ident,
                name,
                str(data.get("type") or "general"),
                payload["description"],
                payload["agent_id"],
                _json(payload["model_config"]),
                payload["system_prompt"],
                _json(payload["tools"]),
                _json(payload["capabilities"]),
                _json(payload["policies"]),
                _json(payload["knowledge_scope"]),
                _json(payload["session_config"]),
                payload["status"],
                _json(payload["metadata"]),
                created_at,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_department(ident)


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
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE departments SET status='archived',updated_at=? WHERE id=?",
            (_now(), department_id),
        )
        conn.commit()
    finally:
        conn.close()
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
