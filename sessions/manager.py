"""Durable runtime sessions stored directly as Kristall journal records."""

from __future__ import annotations

import re
import uuid
from typing import Any, Optional

from agent_memory.client import get_memory_client
from core.clock import unix_seconds
from storage.db import memory_file_path

_SESSION_PREFIX = "runtime_session:"
_SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_-]?key|authorization|password|passwd|secret|token|credential|cookie|private[_-]?key)",
    re.IGNORECASE,
)


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
    session_id: Optional[str] = None, metadata: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    session_id = session_id or str(uuid.uuid4())
    now = unix_seconds()
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


def get_session(session_id: str) -> Optional[dict[str, Any]]:
    value = get_memory_client(memory_file_path()).get(_key(session_id))
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("id") != str(session_id):
        # A record that is not this session is treated as absent, not as a server error.
        return None
    return value


def restore_session(session_id: str) -> Optional[dict[str, Any]]:
    key = _key(session_id)
    with get_memory_client(memory_file_path()).transaction() as tx:
        record = tx.get(key)
        if record is None:
            return None
        if not isinstance(record, dict) or record.get("id") != str(session_id):
            raise ValueError("invalid runtime session record")
        if record.get("status") == "active":
            record["updated_at"] = unix_seconds()
            tx.set(key, record)
        return record

