"""Persistence helpers for user runtime sessions."""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from db import get_conn


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def create_session(session_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    session_id = session_id or str(uuid.uuid4())
    now = _now()
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
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
