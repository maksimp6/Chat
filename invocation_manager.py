"""Invocation lifecycle and persistence for serverless execution."""

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from db import get_conn
from invocation_context import InvocationContext
from runtime_migrations import init_runtime_tables
from session_manager import create_session, restore_session


_SENSITIVE_KEY_RE = re.compile(r"(?:api[_-]?key|authorization|password|passwd|secret|token|credential|cookie|private[_-]?key)", re.IGNORECASE)


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _sanitize_metadata(value: Any) -> Any:
    """Remove credential-like metadata before it reaches persistent storage."""
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


def _resolve_request_session_id(session_id: str, conversation_id: str) -> str:
    """Honor an explicit /api/chat session while preserving legacy behavior."""
    if session_id != conversation_id:
        return session_id
    try:
        from flask import has_request_context, request
        if has_request_context():
            data = request.get_json(silent=True) or {}
            explicit = data.get("session_id")
            if explicit:
                return str(explicit)
    except Exception:
        pass
    return session_id


def create_invocation(
    session_id: str,
    conversation_id: str,
    metadata: Optional[Dict[str, Any]] = None,
    *,
    create_missing_session: bool = True,
) -> InvocationContext:
    """Create a request-scoped invocation and persist its correlation IDs.

    ``/api/chat`` keeps legacy behavior by creating a session on first use.
    Explicit runtime APIs can disable that behavior so a missing session is a
    real lifecycle error rather than an implicit side effect.
    """
    session_id = _resolve_request_session_id(session_id, conversation_id)
    init_runtime_tables()
    session = restore_session(session_id)
    if not session:
        if not create_missing_session:
            raise ValueError(f"Session not found: {session_id}")
        session = create_session(
            session_id,
            metadata={"conversation_id": conversation_id, "legacy": True},
        )

    invocation_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    now = _now()
    persisted_metadata = _sanitize_metadata(metadata or {})
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO invocations
               (id, session_id, conversation_id, trace_id, status, metadata_json,
                trace_json, created_at, started_at)
               VALUES (?, ?, ?, ?, 'created', ?, '{}', ?, NULL)""",
            (
                invocation_id,
                session_id,
                conversation_id,
                trace_id,
                json.dumps(persisted_metadata, ensure_ascii=False),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return InvocationContext(
        session_id=session_id,
        conversation_id=conversation_id,
        invocation_id=invocation_id,
        trace_id=trace_id,
        metadata=persisted_metadata,
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


def persist_invocation_trace(invocation_id: str, trace: Any) -> bool:
    """Persist the complete invocation-owned trace independently of messages."""
    if hasattr(trace, "finalize"):
        trace = trace.finalize()
    sanitized = _sanitize_metadata(trace or {})
    conn = get_conn()
    try:
        cur = conn.execute(
            "UPDATE invocations SET trace_json = ? WHERE id = ?",
            (json.dumps(sanitized, ensure_ascii=False), invocation_id),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def finish_invocation(invocation_id: str, result: Any = None) -> bool:
    return _complete(invocation_id, "completed", result=result)


def fail_invocation(invocation_id: str, error: Any = None) -> bool:
    return _complete(invocation_id, "failed", error=error)


def cancel_invocation(invocation_id: str, reason: Any = None) -> bool:
    return _complete(invocation_id, "cancelled", error=reason)


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
                json.dumps(_sanitize_metadata(result), ensure_ascii=False) if result is not None else None,
                json.dumps(_sanitize_metadata(error), ensure_ascii=False) if error is not None else None,
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
        "trace": decode(row["trace_json"] if "trace_json" in row.keys() else None, {}),
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
    }
