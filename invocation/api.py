"""REST helpers for persisted invocation lifecycle and traces."""

from typing import Any

from invocation.host import bound, get_messages
from invocation.manager import get_invocation


def _sanitize_trace(value: Any) -> Any:
    """Apply the same trace-level secret filtering before API exposure."""
    try:
        return bound.ExecutionTrace._sanitize_trace_value(value)
    except Exception:
        # Never expose unsanitized trace data when the sanitizer fails.
        return "[trace unavailable: sanitization failed]"


def _visible_invocation(invocation: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": invocation.get("id"),
        "session_id": invocation.get("session_id"),
        "conversation_id": invocation.get("conversation_id"),
        "trace_id": invocation.get("trace_id"),
        "status": invocation.get("status"),
        "metadata": _sanitize_trace(invocation.get("metadata") or {}),
        "result": _sanitize_trace(invocation.get("result")),
        "error": _sanitize_trace(invocation.get("error")),
        "created_at": invocation.get("created_at"),
        "started_at": invocation.get("started_at"),
        "completed_at": invocation.get("completed_at"),
    }


def get_invocation_status(invocation_id: str) -> dict[str, Any] | None:
    invocation = get_invocation(invocation_id)
    if invocation is None:
        return None
    return _visible_invocation(invocation)


def get_invocation_trace(invocation_id: str) -> dict[str, Any] | None:
    """Return the trace persisted directly on the invocation.

    Message-level trace storage remains a compatibility fallback for older
    invocations created before the dedicated invocation trace column existed.
    """
    invocation = get_invocation(invocation_id)
    if invocation is None:
        return None

    persisted = invocation.get("trace") or {}
    if persisted:
        return _sanitize_trace(persisted)

    for message in reversed(get_messages(invocation["conversation_id"])):
        trace = message.get("trace") or {}
        context = trace.get("context") or {}
        if context.get("invocation_id") == invocation_id or trace.get(
            "trace_id"
        ) == invocation.get("trace_id"):
            return _sanitize_trace(trace)

    return {
        "trace_id": invocation.get("trace_id"),
        "context": {
            "invocation_id": invocation.get("id"),
            "session_id": invocation.get("session_id"),
            "conversation_id": invocation.get("conversation_id"),
            "trace_id": invocation.get("trace_id"),
        },
        "responses": [],
        "tool_calls": [],
        "events": [],
        "errors": [],
    }


def _message_by_id(conversation_id: str, message_id: int) -> dict[str, Any] | None:
    for message in get_messages(conversation_id):
        if message.get("id") == message_id:
            return message
    return None


def get_message_trace(conversation_id: str, message_id: int) -> dict[str, Any] | None:
    """The trace of one stored message, sanitized the same way as every trace API.

    A message written by a turn keeps a reference to its invocation, and the trace is read
    from there; a message that keeps its trace inline (older records) is returned as stored.
    None when the message is not in the conversation."""
    message = _message_by_id(str(conversation_id), int(message_id))
    if message is None:
        return None
    trace = message.get("trace") or {}
    if trace.get("storage") == "invocation" and trace.get("invocation_id"):
        invocation_id = str(trace["invocation_id"])
        status = get_invocation_status(invocation_id)
        return {
            "message_id": message.get("id"),
            "invocation_id": invocation_id,
            "status": status.get("status") if status else None,
            "trace": get_invocation_trace(invocation_id),
        }
    return {
        "message_id": message.get("id"),
        "invocation_id": None,
        "status": None,
        "trace": _sanitize_trace(trace) if trace else None,
    }


def list_message_traces(conversation_id: str) -> list[dict[str, Any]]:
    """The messages of a conversation that carry a trace, oldest first, without trace bodies."""
    rows = []
    for message in get_messages(str(conversation_id)):
        trace = message.get("trace") or {}
        if not trace:
            continue
        rows.append(
            {
                "message_id": message.get("id"),
                "role": message.get("role"),
                "source": message.get("source"),
                "created_at": message.get("created_at"),
                "invocation_id": trace.get("invocation_id")
                if trace.get("storage") == "invocation"
                else None,
                "inline": trace.get("storage") != "invocation",
            }
        )
    return rows
