"""Runtime-scoped MCP tool calls, executed through the preview runtime dispatcher boundary."""

from __future__ import annotations

import threading
import json
import uuid
from typing import Any, Mapping, Optional
from mcp_server.host import (
    bind_current_trace,
    bound,
    create_invocation,
    create_invocation_trace,
    fail_invocation,
    finish_invocation,
    persist_invocation_trace,
    reset_current_trace,
    start_invocation,
)


# Runtime-scoped MCP calls enter the same dispatcher boundary as preview code.
# Deployment code registers available runtimes on this dispatcher; credentials
# and the unrestricted registry are deliberately not exposed through MCP.
_dispatcher = None
_dispatcher_lock = threading.Lock()


def runtime_dispatcher() -> Any:
    """The dispatcher for runtime-scoped MCP calls, created once on first use."""
    global _dispatcher
    with _dispatcher_lock:
        if _dispatcher is None:
            _dispatcher = bound.RuntimeDispatcher()
            _dispatcher.register_operation("mcp.tool.call", _execute_mcp_tool)
        return _dispatcher


def _execute_mcp_tool(
    _runtime_context: Any, payload: Mapping[str, Any]
) -> dict[str, Any]:
    return bound.UniversalToolExecutor(bound.registry).execute_with_trace(
        payload["call"], payload["trace"]
    )


class ToolOutputError(RuntimeError):
    """The tool ran, but its result could not be serialized for the MCP client."""

    def __init__(self, message: str, *, invocation_id: str, trace_id: str) -> None:
        super().__init__(message)
        self.invocation_id = invocation_id
        self.trace_id = trace_id


def _handle_call(
    name: str,
    arguments: Any,
    user: Optional[str],
    *,
    runtime_id: str | None = None,
    resource_runtime_id: str | None = None,
) -> dict[str, Any]:
    from mcp_server.protocol import _server_info

    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")

    # The HTTP transport stays stateless, but every external tool call gets a
    # persisted invocation and ExecutionTrace for auditability and correlation.
    correlation_id = str(uuid.uuid4())
    context = create_invocation(
        f"mcp:{correlation_id}",
        f"mcp:{correlation_id}",
        metadata={"source": "chatgpt_mcp", "tool": name},
        user_id=user,
    )
    start_invocation(context.invocation_id)
    trace = create_invocation_trace(context)
    trace.set_request(
        {
            "transport": "mcp",
            "method": "tools/call",
            "tool": name,
            "arguments": arguments,
            "call_id": correlation_id,
            "runtime_id": runtime_id,
            "resource_runtime_id": resource_runtime_id,
        }
    )

    call = bound.UniversalToolCall(
        tool_name=name,
        arguments=arguments,
        transport="mcp",
        call_id=correlation_id,
        trace_id=context.trace_id,
        invocation_id=context.invocation_id,
        user_id=user,
        metadata={"source": "chatgpt_mcp", "runtime_id": runtime_id},
    )

    trace_token = bind_current_trace(trace)
    trace_data = None
    # Set once the invocation outcome is being recorded: errors raised from there
    # on propagate as they are, without a second failure record.
    invocation_closed = False
    try:
        if runtime_id:
            result = runtime_dispatcher().dispatch(
                runtime_id,
                "mcp.tool.call",
                {"call": call, "trace": trace},
                resource_runtime_id=resource_runtime_id,
                caller_owner_id=user,
            )
        else:
            result = bound.UniversalToolExecutor(bound.registry).execute_with_trace(
                call, trace
            )
        trace.add_event(
            "mcp_tool_call_completed",
            {
                "tool": name,
                "call_id": correlation_id,
                "success": bool(result.get("success")),
            },
        )
        trace_data = trace.finalize()
        persist_invocation_trace(context.invocation_id, trace_data)

        if not result.get("success"):
            invocation_closed = True
            fail_invocation(
                context.invocation_id,
                error={
                    "tool": name,
                    "error": result.get("error"),
                    "phase": (result.get("metadata") or {}).get("phase"),
                },
            )
            phase = (result.get("metadata") or {}).get("phase")
            if phase == "transport":
                raise LookupError(
                    result.get("error") or "Tool transport is not supported"
                )
            if phase == "validation":
                raise ValueError(result.get("error") or "Tool input validation failed")
            if phase == "authorization":
                raise PermissionError(
                    result.get("error") or "Tool authorization denied"
                )
            if phase == "approval_required":
                raise PermissionError(result.get("error") or "Tool approval required")
            if phase == "execution":
                execution_error = str(result.get("error") or "Tool execution failed")
                if execution_error.startswith("LookupError:"):
                    raise LookupError(execution_error.split(":", 1)[1].strip())
            raise RuntimeError(result.get("error") or "Tool execution failed")

        try:
            text = json.dumps(result.get("data"), ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            # The tool already ran: this is an output failure, not invalid input.
            invocation_closed = True
            fail_invocation(
                context.invocation_id,
                error={
                    "tool": name,
                    "error": f"Tool output is not JSON serializable: {exc}",
                    "phase": "output",
                },
            )
            raise ToolOutputError(
                "Tool output serialization failed",
                invocation_id=context.invocation_id,
                trace_id=context.trace_id,
            ) from exc

        invocation_closed = True
        finish_invocation(
            context.invocation_id,
            result={"tool": name, "success": True, "trace_id": context.trace_id},
        )
        return {
            "structuredContent": result.get("data"),
            "content": [
                {
                    "type": "text",
                    "text": text,
                }
            ],
            "_meta": {
                "serverInfo": _server_info(),
                "toolResult": result,
                "trace_id": context.trace_id,
                "invocation_id": context.invocation_id,
            },
        }
    except (
        bound.RuntimeScopeViolation,
        bound.RuntimeNotFound,
        bound.RuntimeOperationNotFound,
    ) as exc:
        # Persist the violation for operators, but return a stable error that
        # cannot reveal runtime ids, owners, connector names, or credentials.
        trace.add_event("mcp_runtime_access_denied", {"reason": type(exc).__name__})
        trace.record_error("mcp_runtime_dispatch", type(exc).__name__)
        persist_invocation_trace(context.invocation_id, trace.finalize())
        fail_invocation(
            context.invocation_id, error={"tool": name, "error": "runtime unavailable"}
        )
        raise PermissionError("Runtime scope is unavailable") from exc
    except Exception as exc:
        if invocation_closed:
            raise
        if trace_data is None:
            # A finalized trace cannot take the error; recording it would replace exc.
            if not trace.finalized:
                trace.record_error("mcp_tool_call", str(exc), exception=exc)
            trace_data = trace.finalize()
            persist_invocation_trace(context.invocation_id, trace_data)
        fail_invocation(context.invocation_id, error={"tool": name, "error": str(exc)})
        raise
    finally:
        reset_current_trace(trace_token)


def __getattr__(name: str) -> Any:
    """Compatibility for ``mcp_runtime_dispatcher``, now created on first use."""
    if name == "mcp_runtime_dispatcher":
        return runtime_dispatcher()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
