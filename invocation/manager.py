"""Invocation lifecycle and trace persistence on native Kristall."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import re
import time
import uuid
from typing import Any

from invocation.context import InvocationContext
from invocation.host import (
    add_message,
    bound,
    create_session,
    file_court_case,
    get_memory_client,
    get_messages,
    memory_file_path,
    restore_session,
    sanitize_metadata,
)

logger = logging.getLogger("logos.invocation.manager")

_INVOCATION_PREFIX = "runtime_invocation:"
_VALUE_CHUNK_PREFIX = "runtime_invocation_value:"
# v1 chunks hold the JSON as is; v2 chunks hold it gzip-compressed. Both are read.
_VALUE_FORMAT = "kristall-invocation-value-v1"
_VALUE_FORMAT_GZIP = "kristall-invocation-value-v2"


def _value_chunk_bytes() -> int:
    return bound.MAX_JOURNAL_FRAME_BYTES // 4


def _inline_value_bytes() -> int:
    return bound.MAX_JOURNAL_FRAME_BYTES // 2


def _sanitize_for_storage(value: Any) -> Any:
    """Remove credential-like keys and credential-like text before a value is persisted."""
    sanitized = sanitize_metadata(value)
    try:
        return bound.ExecutionTrace._sanitize_trace_value(sanitized)
    except (ImportError, AttributeError):
        return sanitized


def _key(invocation_id: str) -> str:
    return _INVOCATION_PREFIX + str(invocation_id)


def _resolve_request_session_id(session_id: str, conversation_id: str) -> str:
    """Honor an explicit /api/chat session when it differs from conversation_id."""
    if session_id != conversation_id:
        return session_id
    try:
        from webframe import has_request_context, request

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
    metadata: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> InvocationContext:
    session_id = _resolve_request_session_id(session_id, conversation_id)
    session = restore_session(session_id)
    if session is None:
        owner = str(user_id).strip() if user_id and str(user_id).strip() else None
        try:
            create_session(
                session_id,
                metadata={
                    "conversation_id": conversation_id,
                    **({"user_id": owner} if owner else {}),
                },
            )
        except ValueError:
            # Concurrent first use may have created the same native session.
            if restore_session(session_id) is None:
                raise

    invocation_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    now = int(time.time())
    persisted_metadata = sanitize_metadata(metadata or {})
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
        record["started_at"] = int(time.time())
        tx.set(key, record)
        return True


def _prepare_value(
    invocation_id: str, field: str, value: Any, *, force_chunks: bool = False
) -> tuple[Any, dict[str, Any] | None]:
    sanitized = _sanitize_for_storage(value)
    try:
        encoded = json.dumps(
            sanitized, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise bound.KristallError("unsupported JSON value") from exc
    if len(encoded) <= _inline_value_bytes() and not force_chunks:
        return sanitized, None
    # A trace repeats the tool schemas and requests on every step: stored gzip-compressed,
    # it takes a fraction of the space on disk and in Kristall's RAM state.
    encoded = gzip.compress(encoded, compresslevel=6, mtime=0)

    # Chunk transactions retain Kristall's native frame/validation limits.
    # The caller publishes the manifest last in its lifecycle transaction.
    generation = uuid.uuid4().hex
    count = (len(encoded) + _value_chunk_bytes() - 1) // _value_chunk_bytes()
    prefix = f"{_VALUE_CHUNK_PREFIX}{invocation_id}:{field}:{generation}:"
    manifest = {
        "format": _VALUE_FORMAT_GZIP,
        "generation": generation,
        "chunks": count,
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }
    store = get_memory_client(memory_file_path())
    try:
        for index in range(count):
            part = encoded[
                index * _value_chunk_bytes() : (index + 1) * _value_chunk_bytes()
            ]
            with store.transaction() as tx:
                tx.set(
                    prefix + str(index),
                    {"data": base64.b64encode(part).decode("ascii")},
                )
    except Exception:
        _discard_prepared_chunks(invocation_id, field, manifest)
        raise
    return None, manifest


def _assign_value(
    record: dict[str, Any], field: str, prepared: tuple[Any, dict[str, Any] | None]
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
    if record is None or (
        status is not None and record["status"] not in {"created", "running"}
    ):
        return False
    force_chunks = False
    while True:
        prepared: dict[str, tuple[Any, dict[str, Any] | None]] = {}
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
                        record["completed_at"] = int(time.time())
                    for field, value in prepared.items():
                        _assign_value(record, field, value)
                    tx.set(key, record)
                    published = True
        except bound.KristallLimitError:
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
    invocation_id: str, prepared: dict[str, tuple[Any, dict[str, Any] | None]]
) -> None:
    for field, (_value, manifest) in prepared.items():
        _discard_prepared_chunks(invocation_id, field, manifest)


def _discard_prepared_chunks(
    invocation_id: str, field: str, manifest: dict[str, Any] | None
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
        or manifest.get("format") not in {_VALUE_FORMAT, _VALUE_FORMAT_GZIP}
        or not isinstance(manifest.get("generation"), str)
        or not re.fullmatch(r"[0-9a-f]{32}", manifest["generation"])
        or type(manifest.get("chunks")) is not int
        or type(manifest.get("bytes")) is not int
        or manifest["bytes"] < 1
        or manifest["chunks"]
        != (manifest["bytes"] + _value_chunk_bytes() - 1) // _value_chunk_bytes()
        or not isinstance(manifest.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", manifest["sha256"])
    ):
        raise bound.KristallError("invalid invocation value manifest")
    store = get_memory_client(memory_file_path())
    prefix = f"{_VALUE_CHUNK_PREFIX}{invocation_id}:{field}:{manifest['generation']}:"
    pieces = []
    for index in range(manifest["chunks"]):
        chunk = store.get(prefix + str(index))
        if not isinstance(chunk, dict) or not isinstance(chunk.get("data"), str):
            raise bound.KristallError("missing invocation value chunk")
        try:
            part = base64.b64decode(chunk["data"], validate=True)
        except (ValueError, UnicodeEncodeError) as exc:
            raise bound.KristallError("invalid invocation value chunk") from exc
        expected = min(
            _value_chunk_bytes(), manifest["bytes"] - index * _value_chunk_bytes()
        )
        if len(part) != expected:
            raise bound.KristallError("invalid invocation value chunk size")
        pieces.append(part)
    encoded = b"".join(pieces)
    if hashlib.sha256(encoded).hexdigest() != manifest["sha256"]:
        raise bound.KristallError("invocation value integrity mismatch")
    try:
        if manifest["format"] == _VALUE_FORMAT_GZIP:
            encoded = gzip.decompress(encoded)
        return json.loads(encoded.decode("utf-8"))
    except (OSError, EOFError, ValueError, UnicodeDecodeError) as exc:
        raise bound.KristallError("invalid invocation value encoding") from exc


def finish_invocation(invocation_id: str, result: Any = None) -> bool:
    return _complete(invocation_id, "completed", result=result)


def fail_invocation(invocation_id: str, error: Any = None) -> bool:
    published = _complete(invocation_id, "failed", error=error)
    _file_court_case(invocation_id, error)
    return published


def _file_court_case(invocation_id: str, error: Any) -> None:
    """Every failed invocation is a Court case (AGENTS.md, «Ошибки и дела Суда»).

    Filing must never change how the failure itself is published."""
    try:
        record = get_memory_client(memory_file_path()).get(_key(invocation_id))
        record = record if isinstance(record, dict) else {}
        file_court_case(
            {
                "invocation_id": str(invocation_id),
                "conversation_id": record.get("conversation_id"),
                "session_id": record.get("session_id"),
                "trace_id": record.get("trace_id"),
                "created_at": record.get("created_at"),
                "error": _sanitize_for_storage(error),
            }
        )
    except Exception:
        logger.exception("court case for invocation %s was not filed", invocation_id)


INTERRUPTED_REPLY = (
    "⚠️ Ошибка: ход прерван перезапуском Logos (обновление или сбой сервера), "
    "ответ не был получен. Повторите запрос."
)


def fail_interrupted_invocations(before: float) -> int:
    """Fail every invocation a previous process left created or running (#1710).

    Logos is one process, so a turn still open from before ``before`` (this
    process's boot) can never finish: a deploy or crash killed it. Each one is
    failed, which files its Court case, and a dialog whose last message is still
    the question gets an answer saying so, instead of nothing."""
    client = get_memory_client(memory_file_path())
    failed = 0
    for key, record in client.items(_INVOCATION_PREFIX).items():
        if not isinstance(record, dict) or record.get("status") not in {"created", "running"}:
            continue
        if (record.get("created_at") or 0) >= before:
            continue
        invocation_id = str(record.get("id") or key[len(_INVOCATION_PREFIX):])
        error = {
            "code": "interrupted_by_restart",
            "message": "the Logos process stopped before the turn finished",
            "status_at_restart": record.get("status"),
        }
        if not fail_invocation(invocation_id, error):
            continue
        failed += 1
        conversation_id = record.get("conversation_id")
        try:
            messages = get_messages(conversation_id) if conversation_id else []
            if messages and messages[-1].get("role") == "user":
                add_message(
                    conversation_id,
                    "assistant",
                    INTERRUPTED_REPLY,
                    cost=None,
                    source="interrupted:" + invocation_id,
                    deduplicate_source=True,
                    trace={"invocation_id": invocation_id, "error": error},
                )
        except Exception:
            logger.exception("interrupted turn %s was not reported in its dialog", invocation_id)
    if failed:
        logger.error("[BOOT] %d turn(s) were interrupted by the last restart", failed)
    return failed


def _complete(
    invocation_id: str, status: str, result: Any = None, error: Any = None
) -> bool:
    return _publish_invocation_values(
        invocation_id, {"result": result, "error": error}, status=status
    )


def conversation_turn_running(conversation_id: str) -> bool:
    """True while an invocation of this conversation is created or running."""
    for record in get_memory_client(memory_file_path()).items(_INVOCATION_PREFIX).values():
        if (
            isinstance(record, dict)
            and record.get("conversation_id") == str(conversation_id)
            and record.get("status") in {"created", "running"}
        ):
            return True
    return False


def get_invocation(invocation_id: str) -> dict[str, Any] | None:
    value = get_memory_client(memory_file_path()).get(_key(invocation_id))
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("id") != str(invocation_id):
        # A record that is not this invocation is treated as absent, as in get_session,
        # not as a server error.
        return None
    for field in ("trace", "result", "error"):
        if field + "_chunks" in value:
            value[field] = _read_chunked_value(
                invocation_id, field, value.pop(field + "_chunks")
            )
    return value
