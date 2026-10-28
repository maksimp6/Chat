"""Invocation lifecycle and trace persistence on native Kristall."""

from __future__ import annotations

import re
import uuid
import base64
import hashlib
import json
from typing import Any, Optional

from agent_memory.client import get_memory_client
from core.clock import unix_seconds
from storage.db import memory_file_path
from invocation.context import InvocationContext
from sessions.manager import create_session, restore_session
from kristall import KristallError, KristallLimitError
from kristall.store import MAX_JOURNAL_FRAME_BYTES

import logging

logger = logging.getLogger(__name__)

_INVOCATION_PREFIX = "runtime_invocation:"
_VALUE_CHUNK_PREFIX = "runtime_invocation_value:"
_VALUE_CHUNK_BYTES = MAX_JOURNAL_FRAME_BYTES // 4
_INLINE_VALUE_BYTES = MAX_JOURNAL_FRAME_BYTES // 2
# "token" matches credential names such as access_token, but not usage counters such as
# input_tokens, total_tokens or output_tokens, which the saved trace needs.
_SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_-]?key|authorization|password|passwd|secret|credential|cookie|private[_-]?key"
    r"|token(?!s(?![A-Za-z])))",
    re.IGNORECASE,
)


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


def _sanitize_for_storage(value: Any) -> Any:
    """Remove credential-like keys and credential-like text before a value is persisted."""
    sanitized = _sanitize_metadata(value)
    try:
        from observability.trace_manager import ExecutionTrace

        return ExecutionTrace._sanitize_trace_value(sanitized)
    except (ImportError, AttributeError):
        return sanitized


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
        logger.debug("[INVOCATION] session id not read from request", exc_info=True)
    return session_id


def create_invocation(
    session_id: str,
    conversation_id: str,
    metadata: Optional[dict[str, Any]] = None,
    user_id: Optional[str] = None,
    *,
    create_missing_session: bool = True,
) -> InvocationContext:
    session_id = _resolve_request_session_id(session_id, conversation_id)
    session = restore_session(session_id)
    if session is None:
        if not create_missing_session:
            raise ValueError(f"Session not found: {session_id}")
        legacy_owner = str(user_id).strip() if user_id and str(user_id).strip() else None
        try:
            create_session(
                session_id,
                metadata={
                    "conversation_id": conversation_id,
                    "legacy": True,
                    **({"user_id": legacy_owner} if legacy_owner else {}),
                },
            )
        except ValueError:
            # Concurrent first use may have created the same native session.
            if restore_session(session_id) is None:
                raise

    invocation_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    now = unix_seconds()
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
        record["started_at"] = unix_seconds()
        tx.set(key, record)
        return True


def _prepare_value(
    invocation_id: str, field: str, value: Any, *, force_chunks: bool = False
) -> tuple[Any, Optional[dict[str, Any]]]:
    sanitized = _sanitize_for_storage(value)
    try:
        encoded = json.dumps(
            sanitized, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise KristallError("unsupported JSON value") from exc
    if len(encoded) <= _INLINE_VALUE_BYTES and not force_chunks:
        return sanitized, None

    # Chunk transactions retain Kristall's native frame/validation limits.
    # The caller publishes the manifest last in its lifecycle transaction.
    generation = uuid.uuid4().hex
    count = (len(encoded) + _VALUE_CHUNK_BYTES - 1) // _VALUE_CHUNK_BYTES
    prefix = f"{_VALUE_CHUNK_PREFIX}{invocation_id}:{field}:{generation}:"
    manifest = {
        "format": "kristall-invocation-value-v1",
        "generation": generation,
        "chunks": count,
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }
    store = get_memory_client(memory_file_path())
    try:
        for index in range(count):
            part = encoded[index * _VALUE_CHUNK_BYTES : (index + 1) * _VALUE_CHUNK_BYTES]
            with store.transaction() as tx:
                tx.set(prefix + str(index), {"data": base64.b64encode(part).decode("ascii")})
    except Exception:
        _discard_prepared_chunks(invocation_id, field, manifest)
        raise
    return None, manifest


def _assign_value(
    record: dict[str, Any], field: str, prepared: tuple[Any, Optional[dict[str, Any]]]
) -> None:
    value, manifest = prepared
    record[field] = value
    if manifest is None:
        record.pop(field + "_chunks", None)
    else:
        record[field + "_chunks"] = manifest


def persist_invocation_trace(invocation_id: str, trace: Any) -> bool:
    """Store complete traces inline or as verified native Kristall chunks.

    Each immutable chunk fits one journal frame. The manifest is published last;
    failed writes retain the prior authoritative trace. Prior/unreferenced chunks
    remain available, so a reader of an older manifest cannot lose its chunks.
    """
    if hasattr(trace, "finalize"):
        trace = trace.finalize()
    return _publish_invocation_values(invocation_id, {"trace": trace or {}})


def _publish_invocation_values(
    invocation_id: str, values: dict[str, Any], *, status: str | None = None
) -> bool:
    """Publish prepared fields together; retry only a typed native size rejection."""
    key = _key(invocation_id)
    store = get_memory_client(memory_file_path())
    record = store.get(key)
    if record is None or (status is not None and record["status"] not in {"created", "running"}):
        return False
    force_chunks = False
    while True:
        prepared: dict[str, tuple[Any, Optional[dict[str, Any]]]] = {}
        try:
            for field, value in values.items():
                prepared[field] = _prepare_value(
                    invocation_id, field, value, force_chunks=force_chunks
                )
        except Exception:
            _discard_values(invocation_id, prepared)
            raise
        published = False
        try:
            with store.transaction() as tx:
                record = tx.get(key)
                if record is not None and (
                    status is None or record["status"] in {"created", "running"}
                ):
                    if status is not None:
                        record["status"] = status
                        record["completed_at"] = unix_seconds()
                    for field, value in prepared.items():
                        _assign_value(record, field, value)
                    tx.set(key, record)
                    published = True
        except KristallLimitError:
            _discard_values(invocation_id, prepared)
            if force_chunks:
                raise
            force_chunks = True
            continue
        except Exception:
            _discard_values(invocation_id, prepared)
            raise
        if not published:
            _discard_values(invocation_id, prepared)
        return published


def _discard_values(
    invocation_id: str, prepared: dict[str, tuple[Any, Optional[dict[str, Any]]]]
) -> None:
    for field, (_value, manifest) in prepared.items():
        _discard_prepared_chunks(invocation_id, field, manifest)


def _discard_prepared_chunks(
    invocation_id: str, field: str, manifest: Optional[dict[str, Any]]
) -> None:
    """Remove the chunks of a value that was never published, so a failed write leaves nothing behind."""
    if not manifest:
        return
    store = get_memory_client(memory_file_path())
    prefix = f"{_VALUE_CHUNK_PREFIX}{invocation_id}:{field}:{manifest['generation']}:"
    try:
        for index in range(manifest["chunks"]):
            with store.transaction() as tx:
                if tx.get(prefix + str(index)) is not None:
                    tx.delete(prefix + str(index))
    except Exception:
        # Best effort: the caller reports the original failure, not this cleanup.
        return


def _read_chunked_value(invocation_id: str, field: str, manifest: Any) -> Any:
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != "kristall-invocation-value-v1"
        or not isinstance(manifest.get("generation"), str)
        or not re.fullmatch(r"[0-9a-f]{32}", manifest["generation"])
        or type(manifest.get("chunks")) is not int
        or type(manifest.get("bytes")) is not int
        or manifest["bytes"] < 1
        or manifest["chunks"] != (manifest["bytes"] + _VALUE_CHUNK_BYTES - 1) // _VALUE_CHUNK_BYTES
        or not isinstance(manifest.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", manifest["sha256"])
    ):
        raise KristallError("invalid invocation value manifest")
    store = get_memory_client(memory_file_path())
    prefix = f"{_VALUE_CHUNK_PREFIX}{invocation_id}:{field}:{manifest['generation']}:"
    pieces = []
    for index in range(manifest["chunks"]):
        chunk = store.get(prefix + str(index))
        if not isinstance(chunk, dict) or not isinstance(chunk.get("data"), str):
            raise KristallError("missing invocation value chunk")
        try:
            part = base64.b64decode(chunk["data"], validate=True)
        except (ValueError, UnicodeEncodeError) as exc:
            raise KristallError("invalid invocation value chunk") from exc
        expected = min(_VALUE_CHUNK_BYTES, manifest["bytes"] - index * _VALUE_CHUNK_BYTES)
        if len(part) != expected:
            raise KristallError("invalid invocation value chunk size")
        pieces.append(part)
    encoded = b"".join(pieces)
    if hashlib.sha256(encoded).hexdigest() != manifest["sha256"]:
        raise KristallError("invocation value integrity mismatch")
    try:
        return json.loads(encoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise KristallError("invalid invocation value encoding") from exc


def finish_invocation(invocation_id: str, result: Any = None) -> bool:
    return _complete(invocation_id, "completed", result=result)


def fail_invocation(invocation_id: str, error: Any = None) -> bool:
    return _complete(invocation_id, "failed", error=error)


def cancel_invocation(invocation_id: str, reason: Any = None) -> bool:
    return _complete(invocation_id, "cancelled", error=reason)


def _complete(invocation_id: str, status: str, result: Any = None, error: Any = None) -> bool:
    return _publish_invocation_values(
        invocation_id, {"result": result, "error": error}, status=status
    )


def get_invocation_owner(invocation_id: str) -> Optional[str]:
    """Return the owner an invocation was created for, without reading its trace or result."""
    value = get_memory_client(memory_file_path()).get(_key(invocation_id))
    if not isinstance(value, dict) or value.get("id") != str(invocation_id):
        return None
    return value.get("user_id")


def get_invocation(invocation_id: str) -> Optional[dict[str, Any]]:
    value = get_memory_client(memory_file_path()).get(_key(invocation_id))
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("id") != str(invocation_id):
        raise ValueError("invalid invocation record")
    for field in ("trace", "result", "error"):
        if field + "_chunks" in value:
            value[field] = _read_chunked_value(invocation_id, field, value.pop(field + "_chunks"))
    return value

