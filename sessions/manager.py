"""Durable runtime sessions stored directly as Kristall journal records."""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

from sessions.host import get_memory_client, memory_file_path

_SESSION_PREFIX = "runtime_session:"
# "token" matches credential names such as access_token, but not usage counters such as
# input_tokens or max_tokens, which are kept in the metadata.
_SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_-]?key|authorization|password|passwd|secret|token(?!s(?![A-Za-z]))|credential|cookie|private[_-]?key)",
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
    session_id: str | None = None, metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    session_id = session_id or str(uuid.uuid4())
    now = int(time.time())
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


def get_session(session_id: str) -> dict[str, Any] | None:
    value = get_memory_client(memory_file_path()).get(_key(session_id))
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("id") != str(session_id):
        # A record that is not this session is treated as absent, not as a server error.
        return None
    return value


def restore_session(session_id: str) -> dict[str, Any] | None:
    key = _key(session_id)
    with get_memory_client(memory_file_path()).transaction() as tx:
        record = tx.get(key)
        if record is None:
            return None
        if not isinstance(record, dict) or record.get("id") != str(session_id):
            raise ValueError("invalid runtime session record")
        if record.get("status") == "active":
            record["updated_at"] = int(time.time())
            tx.set(key, record)
        return record


def update_session(
    session_id: str,
    metadata: dict[str, Any] | None = None,
    status: str | None = None,
) -> dict[str, Any] | None:
    if status is not None and status not in {
        "active",
        "completed",
        "failed",
        "cancelled",
    }:
        raise ValueError(f"Unsupported session status: {status}")
    key = _key(session_id)
    with get_memory_client(memory_file_path()).transaction() as tx:
        record = tx.get(key)
        if record is None:
            return None
        if not isinstance(record, dict) or record.get("id") != str(session_id):
            raise ValueError("invalid runtime session record")
        record["updated_at"] = int(time.time())
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
                record["completed_at"] = int(time.time())
            else:
                # A session made active again is no longer completed.
                record["completed_at"] = None
        tx.set(key, record)
        return record
