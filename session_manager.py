"""Persistence helpers for user runtime sessions."""

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from db import get_conn


_SENSITIVE_KEY_RE = re.compile(r"(?:api[_-]?key|authorization|password|passwd|secret|token|credential|cookie|private[_-]?key)", re.IGNORECASE)


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _sanitize_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_metadata(item)
            for key, item in value.items()
            if not _SENSITIVE_KEY_RE.search(str(key))
        }
    if isinstance(value, list):
        return [_sanitize_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_metadata(item) for item in value]
    return value


def create_session(session_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    session_id = session_id or str(uuid.uuid4())
    now = _now()
    metadata_json = json.dumps(_sanitize_metadata(metadata or {}), ensure_ascii=False)
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO sessions (id, status, metadata_json, created_at, updated_at)
               VALUES (?, 'active', ?, ?, ?)""",
            (session_id, metadata_json, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return get_session(session_id)


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except (TypeError, ValueError):
        metadata = {}
    return {
        "id": row["id"],
        "status": row["status"],
        "metadata": metadata,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"] if "completed_at" in row.keys() else None,
    }


def restore_session(session_id: str) -> Optional[Dict[str, Any]]:
    session = get_session(session_id)
    if not session:
        return None
    if session["status"] != "active":
        return session
    now = _now()
    conn = get_conn()
    try:
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        conn.commit()
    finally:
        conn.close()
    session["updated_at"] = now
    return session


def update_session(session_id: str, metadata: Optional[Dict[str, Any]] = None, status: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Persist session state without replacing fields that were not supplied."""
    session = get_session(session_id)
    if not session:
        return None
    fields = ["updated_at = ?"]
    values = [_now()]
    if metadata is not None:
        fields.append("metadata_json = ?")
        values.append(json.dumps(_sanitize_metadata(metadata), ensure_ascii=False))
    if status is not None:
        if status not in {"active", "completed", "failed", "cancelled"}:
            raise ValueError(f"Unsupported session status: {status}")
        fields.append("status = ?")
        values.append(status)
        if status in {"completed", "failed", "cancelled"}:
            fields.append("completed_at = ?")
            values.append(_now())
    values.append(session_id)
    conn = get_conn()
    try:
        conn.execute(f"UPDATE sessions SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()
    return get_session(session_id)
