"""Persistence boundary for invocation ExecutionTrace objects."""

import json
from typing import Any, Dict, Optional

from db import get_conn


def save_trace(trace_data: Dict[str, Any]) -> str:
    """Persist one finalized trace and return its trace_id."""
    if not isinstance(trace_data, dict) or not trace_data.get("trace_id"):
        raise ValueError("trace_data.trace_id is required")

    trace_id = str(trace_data["trace_id"])
    try:
        payload = json.dumps(trace_data, ensure_ascii=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"trace_serialization_failed: {type(exc).__name__}") from exc

    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO execution_traces
               (trace_id, session_id, conversation_id, invocation_id, status, trace_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, strftime('%s','now')), strftime('%s','now'))
               ON CONFLICT(trace_id) DO UPDATE SET
                 session_id=excluded.session_id,
                 conversation_id=excluded.conversation_id,
                 invocation_id=excluded.invocation_id,
                 status=excluded.status,
                 trace_json=excluded.trace_json,
                 updated_at=excluded.updated_at""",
            (
                trace_id,
                trace_data.get("session_id"),
                trace_data.get("conversation_id"),
                trace_data.get("invocation_id"),
                trace_data.get("status", "completed"),
                payload,
                trace_data.get("created_at"),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return trace_id


def load_trace(trace_id: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT trace_json FROM execution_traces WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        return json.loads(row["trace_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def export_trace(trace_id: str) -> str:
    """Return the persisted trace as deterministic JSON text."""
    trace = load_trace(trace_id)
    if trace is None:
        raise KeyError(trace_id)
    return json.dumps(trace, ensure_ascii=False, indent=2, sort_keys=True)
