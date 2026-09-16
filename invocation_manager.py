"""Invocation lifecycle and persistence for serverless execution."""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from db import get_conn
from invocation_context import InvocationContext
from runtime_migrations import init_runtime_tables
from session_manager import create_session, restore_session


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def create_invocation(session_id: str, conversation_id: str, metadata: Optional[Dict[str, Any]] = None) -> InvocationContext:
    # /api/chat historically accepted only conversation_id. Ensure the runtime
    # schema exists and create a stable legacy session on first use so existing
    # conversations can participate in the new invocation lifecycle.
    init_runtime_tables()
    session = restore_session(session_id)
    if not session:
        session = create_session(session_id, metadata={"conversation_id": conversation_id, "legacy": True})

    invocation_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    now = _now()
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO invocations
               (id, session_id, conversation_id, trace_id, status, metadata_json, created_at, started_at)
               VALUES (?, ?, ?, ?, 'created', ?, ?, NULL)""",
            (invocation_id, session_id, conversation_id, trace_id,
             json.dumps(metadata or {}, ensure_ascii=False), now),
        )
        conn.commit()
    finally:
        conn.close()

    return InvocationContext(
        session_id=session_id,
        conversation_id=conversation_id,
        invocation_id=invocation_id,
        trace_id=trace_id,
        metadata=metadata or {},
    )


def start_invocation(invocation_id: str) -> bool:
    conn = get_conn()
    try:
        cur = conn.execute(
            "UPDATE invocations SET status = 'running', started_at = ? WHERE id = ? AND status = 'created'",
            (_now(), invocation_id),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def finish_invocation(invocation_id: str, result: Any = None) -> bool:
    return _complete(invocation_id, "completed", result=result)


def fail_invocation(invocation_id: str, error: Any = None) -> bool:
    return _complete(invocation_id, "failed", error=error)


def _complete(invocation_id: str, status: str, result: Any = None, error: Any = None) -> bool:
    now = _now()
    conn = get_conn()
    try:
        cur = conn.execute(
            """UPDATE invocations
               SET status = ?, completed_at = ?, result_json = ?, error_json = ?
               WHERE id = ? AND status IN ('created', 'running')""",
            (
                status,
                now,
                json.dumps(result, ensure_ascii=False) if result is not None else None,
                json.dumps(error, ensure_ascii=False) if error is not None else None,
                invocation_id,
            ),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def get_invocation(invocation_id: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM invocations WHERE id = ?", (invocation_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None

    def decode(value: Any, default: Any) -> Any:
        if not value:
            return default
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default

    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "conversation_id": row["conversation_id"],
        "trace_id": row["trace_id"],
        "status": row["status"],
        "metadata": decode(row["metadata_json"], {}),
        "result": decode(row["result_json"], None),
        "error": decode(row["error_json"], None),
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
    }
