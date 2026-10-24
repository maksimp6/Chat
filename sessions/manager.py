"""Durable runtime sessions stored directly as Kristall journal records."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from agent_memory.client import get_memory_client
from storage.db import memory_file_path

_SESSION_PREFIX = "runtime_session:"
_SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_-]?key|authorization|password|passwd|secret|token|credential|cookie|private[_-]?key)",
    re.IGNORECASE,
)


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


def _key(session_id: str) -> str:
    return _SESSION_PREFIX + str(session_id)


def create_session(
    session_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    session_id = session_id or str(uuid.uuid4())
    now = _now()
    record = {
        "id": session_id,
        "status": "active",
        "metadata": _sanitize_metadata(metadata or {}),
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
    }
    with get_memory_client(memory_file_path()).transaction() as tx:
        if tx.get(_key(session_id)) is not None:
            raise ValueError(f"Session already exists: {session_id}")
        tx.set(_key(session_id), record)
    return record


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    value = get_memory_client(memory_file_path()).get(_key(session_id))
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("id") != str(session_id):
        # A record that is not this session is treated as absent, not as a server error.
        return None
    return value


def restore_session(session_id: str) -> Optional[Dict[str, Any]]:
    key = _key(session_id)
    with get_memory_client(memory_file_path()).transaction() as tx:
        record = tx.get(key)
        if record is None:
            return None
        if not isinstance(record, dict) or record.get("id") != str(session_id):
            raise ValueError("invalid runtime session record")
        if record.get("status") == "active":
            record["updated_at"] = _now()
            tx.set(key, record)
        return record


def update_session(
    session_id: str, metadata: Optional[Dict[str, Any]] = None, status: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    if status is not None and status not in {"active", "completed", "failed", "cancelled"}:
        raise ValueError(f"Unsupported session status: {status}")
    key = _key(session_id)
    with get_memory_client(memory_file_path()).transaction() as tx:
        record = tx.get(key)
        if record is None:
            return None
        if not isinstance(record, dict) or record.get("id") != str(session_id):
            raise ValueError("invalid runtime session record")
        record["updated_at"] = _now()
        if metadata is not None:
            replaced = _sanitize_metadata(metadata)
            # Replacing metadata must not drop the owner recorded at creation.
            owner = (record.get("metadata") or {}).get("user_id")
            if owner is not None and "user_id" not in replaced:
                replaced["user_id"] = owner
            record["metadata"] = replaced
        if status is not None:
            record["status"] = status
            if status in {"completed", "failed", "cancelled"}:
                record["completed_at"] = _now()
        tx.set(key, record)
        return record
