"""Invocation lifecycle and trace persistence on native MemoryStore."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from agent_memory.client import get_memory_client
from storage.db import memory_file_path
from invocation.context import InvocationContext
from sessions.manager import create_session, restore_session

_INVOCATION_PREFIX = "runtime_invocation:"
_SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_-]?key|authorization|password|passwd|secret|token|credential|cookie|private[_-]?key)",
    re.IGNORECASE,
)


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


def _key(invocation_id: str) -> str:
    return _INVOCATION_PREFIX + str(invocation_id)


def _resolve_request_session_id(session_id: str, conversation_id: str) -> str:
    """Honor an explicit /api/chat session when it differs from conversation_id."""
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
    user_id: Optional[str] = None,
    *,
    create_missing_session: bool = True,
) -> InvocationContext:
    session_id = _resolve_request_session_id(session_id, conversation_id)
    session = restore_session(session_id)
    if session is None:
        if not create_missing_session:
            raise ValueError(f"Session not found: {session_id}")
        try:
            create_session(
                session_id,
                metadata={"conversation_id": conversation_id, "legacy": True},
            )
        except ValueError:
            # Concurrent first use may have created the same native session.
            if restore_session(session_id) is None:
                raise

    invocation_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    now = _now()
    persisted_metadata = _sanitize_metadata(metadata or {})
    resolved_user_id = (
        str(user_id).strip() if user_id is not None and str(user_id).strip() else None
    )
    if resolved_user_id is not None:
        persisted_metadata["user_id"] = resolved_user_id
    record = {
        "id": invocation_id,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "trace_id": trace_id,
        "user_id": resolved_user_id,
        "status": "created",
        "metadata": persisted_metadata,
        "result": None,
        "error": None,
        "trace": {},
        "created_at": now,
        "started_at": None,
        "completed_at": None,
    }
    with get_memory_client(memory_file_path()).transaction() as tx:
        tx.set(_key(invocation_id), record)
    return InvocationContext(
        session_id=session_id,
        conversation_id=conversation_id,
        invocation_id=invocation_id,
        trace_id=trace_id,
        user_id=resolved_user_id,
        metadata=persisted_metadata,
    )


def start_invocation(invocation_id: str) -> bool:
    key = _key(invocation_id)
    with get_memory_client(memory_file_path()).transaction() as tx:
        record = tx.get(key)
        if record is None or record["status"] != "created":
            return False
        record["status"] = "running"
        record["started_at"] = _now()
        tx.set(key, record)
        return True


def persist_invocation_trace(invocation_id: str, trace: Any) -> bool:
    """Durably store invocation-owned traces independently of chat messages."""
    if hasattr(trace, "finalize"):
        trace = trace.finalize()
    sanitized = _sanitize_metadata(trace or {})
    key = _key(invocation_id)
    with get_memory_client(memory_file_path()).transaction() as tx:
        record = tx.get(key)
        if record is None:
            return False
        record["trace"] = sanitized
        tx.set(key, record)
        return True


def finish_invocation(invocation_id: str, result: Any = None) -> bool:
    return _complete(invocation_id, "completed", result=result)


def fail_invocation(invocation_id: str, error: Any = None) -> bool:
    return _complete(invocation_id, "failed", error=error)


def cancel_invocation(invocation_id: str, reason: Any = None) -> bool:
    return _complete(invocation_id, "cancelled", error=reason)


def _complete(invocation_id: str, status: str, result: Any = None, error: Any = None) -> bool:
    key = _key(invocation_id)
    with get_memory_client(memory_file_path()).transaction() as tx:
        record = tx.get(key)
        if record is None or record["status"] not in {"created", "running"}:
            return False
        record["status"] = status
        record["completed_at"] = _now()
        record["result"] = _sanitize_metadata(result) if result is not None else None
        record["error"] = _sanitize_metadata(error) if error is not None else None
        tx.set(key, record)
        return True


def get_invocation(invocation_id: str) -> Optional[Dict[str, Any]]:
    value = get_memory_client(memory_file_path()).get(_key(invocation_id))
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("id") != str(invocation_id):
        raise ValueError("invalid invocation record")
    return value
