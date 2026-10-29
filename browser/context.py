"""Call context of one browser tool call: trace events, secrets and the session key.

The tool registry passes the universal call in ``cfg["_universal_context"]``;
only that key is read. Every other ``cfg`` key is ignored on purpose: a user MCP
server whose connector id matches the tool names could otherwise inject
configuration into the browser.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Mapping

logger = logging.getLogger("logos.browser.context")

_DEFAULT_SESSION = "default"


def _call_of(cfg: Mapping[str, Any] | None) -> Any:
    if not isinstance(cfg, Mapping):
        return None
    context = cfg.get("_universal_context")
    if not isinstance(context, Mapping):
        return None
    return context.get("call")


def _metadata_of(call: Any) -> Mapping[str, Any]:
    metadata = getattr(call, "metadata", None)
    return metadata if isinstance(metadata, Mapping) else {}


def _trace_of(call: Any) -> Any:
    trace = _metadata_of(call).get("execution_trace")
    return trace if trace is not None and hasattr(trace, "add_event") else None


def resolve_session_key(args: Mapping[str, Any], cfg: Mapping[str, Any] | None) -> str:
    """The browser session of a call: explicit ``session``, else the dialog's, else "default".

    MCP calls (and calls without a dialog) share "default"; a dialog call uses
    "dialog:<conversation id>" from the call's execution trace.
    """
    explicit = args.get("session") if isinstance(args, Mapping) else None
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    call = _call_of(cfg)
    if call is None:
        return _DEFAULT_SESSION
    if str(getattr(call, "transport", "") or "") == "mcp":
        return _DEFAULT_SESSION
    if _metadata_of(call).get("source") == "chatgpt_mcp":
        return _DEFAULT_SESSION
    trace = _trace_of(call)
    data = getattr(trace, "trace", None) if trace is not None else None
    if not isinstance(data, Mapping):
        return _DEFAULT_SESSION
    context = data.get("context")
    conversation = context.get("conversation_id") if isinstance(context, Mapping) else None
    text = str(conversation or "").strip()
    if not text or text.startswith("mcp:"):
        return _DEFAULT_SESSION
    return "dialog:" + text


class CallContext:
    """What a browser operation needs from its tool call (the engines' EventSink)."""

    def __init__(self, tool: str, args: Mapping[str, Any] | None, cfg: Mapping[str, Any] | None) -> None:
        self.tool = tool
        self.call = _call_of(cfg)
        self.trace = _trace_of(self.call) if self.call is not None else None
        self.transport = str(getattr(self.call, "transport", "") or "") if self.call is not None else ""
        self.session_key = resolve_session_key(args if isinstance(args, Mapping) else {}, cfg)
        # Trace events are recorded on the calling thread only (engine worker
        # threads queue theirs and the operation forwards them).
        self._thread = threading.get_ident()

    def event(self, event_type: str, payload: Mapping[str, Any]) -> None:
        """Add a trace event; no trace, another thread or a finalized trace is a no-op."""
        trace = self.trace
        if trace is None or threading.get_ident() != self._thread:
            return
        try:
            trace.add_event(event_type, dict(payload))
        except RuntimeError as exc:
            logger.debug("trace event %s dropped: %s", event_type, type(exc).__name__)

    def secret(self, value: str | None) -> None:
        """Register a value the trace must redact (passwords, tokens, cookie values)."""
        trace = self.trace
        if trace is None or not isinstance(value, str) or not value:
            return
        register = getattr(trace, "register_sensitive_value", None)
        if register is None:
            return
        try:
            register(value)
        except RuntimeError as exc:
            logger.debug("sensitive value not registered: %s", type(exc).__name__)


__all__ = ["CallContext", "resolve_session_key"]
