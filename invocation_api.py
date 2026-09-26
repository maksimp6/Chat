"""REST helpers for persisted invocation lifecycle and traces."""

from typing import Any, Dict, Optional

from invocation_manager import get_invocation


def _sanitize_trace(value: Any) -> Any:
    """Apply the same trace-level secret filtering before API exposure."""
    try:
        from trace_manager import ExecutionTrace

        return ExecutionTrace._sanitize_trace_value(value)
    except Exception:
        return value


def _visible_invocation(invocation: Dict[str, Any]) -> Dict[str, Any]:
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


def get_invocation_status(invocation_id: str) -> Optional[Dict[str, Any]]:
    invocation = get_invocation(invocation_id)
    if invocation is None:
        return None
    return _visible_invocation(invocation)


def get_invocation_trace(invocation_id: str) -> Optional[Dict[str, Any]]:
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

    from db import get_messages

    for message in reversed(get_messages(invocation["conversation_id"])):
        trace = message.get("trace") or {}
        context = trace.get("context") or {}
        if context.get("invocation_id") == invocation_id or trace.get("trace_id") == invocation.get(
            "trace_id"
        ):
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
