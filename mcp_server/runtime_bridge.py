from __future__ import annotations

import json
import uuid
from typing import Any, Mapping, Optional

from invocation.manager import (
    create_invocation,
    fail_invocation,
    finish_invocation,
    persist_invocation_trace,
    start_invocation,
)
from invocation.trace import create_invocation_trace
from runtime import (
    RuntimeDispatcher,
    RuntimeNotFound,
    RuntimeOperationNotFound,
    RuntimeScopeViolation,
)
from tool_registry import registry
from universal_tool_platform import UniversalToolCall, UniversalToolExecutor


# Runtime-scoped MCP calls enter the same dispatcher boundary as preview code.
# Deployment code registers available runtimes on this dispatcher; credentials
# and the unrestricted registry are deliberately not exposed through MCP.
mcp_runtime_dispatcher = RuntimeDispatcher()


def _owner_id_from_invocation(invocation: Mapping[str, Any]) -> Optional[str]:
    metadata = invocation.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    value = metadata.get("user_id")
    return str(value).strip() if value is not None and str(value).strip() else None


def _authorize_invocation(invocation: Mapping[str, Any], authenticated_user: Optional[str]) -> None:
    """Keep private invocation data scoped to the authenticated user.

    Legacy invocations may predate user ownership.  They are therefore only
    accessible when anonymous/developer access is explicitly enabled.
    """
    from .auth import _auth_mode

    owner_id = _owner_id_from_invocation(invocation)
    if owner_id and authenticated_user and owner_id == authenticated_user:
        return
    if owner_id is None and _auth_mode() == "anonymous":
        return
    if owner_id is None and authenticated_user:
        raise PermissionError("Invocation has no trusted user ownership")
    if owner_id != authenticated_user:
        raise PermissionError("Invocation is not accessible to this user")


def _execute_mcp_tool(_runtime_context: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    return UniversalToolExecutor(registry).execute_with_trace(payload["call"], payload["trace"])


mcp_runtime_dispatcher.register_operation("mcp.tool.call", _execute_mcp_tool)


def _handle_call(
    name: str,
    arguments: Any,
    user: Optional[str],
    *,
    runtime_id: str | None = None,
    resource_runtime_id: str | None = None,
) -> dict[str, Any]:
    from .protocol import _server_info

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

    call = UniversalToolCall(
        tool_name=name,
        arguments=arguments,
        transport="mcp",
        call_id=correlation_id,
        trace_id=context.trace_id,
        invocation_id=context.invocation_id,
        user_id=user,
        metadata={"source": "chatgpt_mcp", "runtime_id": runtime_id},
    )

    try:
        if runtime_id:
            result = mcp_runtime_dispatcher.dispatch(
                runtime_id,
                "mcp.tool.call",
                {"call": call, "trace": trace},
                resource_runtime_id=resource_runtime_id,
                caller_owner_id=user,
            )
        else:
            result = UniversalToolExecutor(registry).execute_with_trace(call, trace)
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
                raise LookupError(result.get("error") or "Tool transport is not supported")
            if phase == "validation":
                raise ValueError(result.get("error") or "Tool input validation failed")
            if phase == "authorization":
                raise PermissionError(result.get("error") or "Tool authorization denied")
            if phase == "approval_required":
                raise PermissionError(result.get("error") or "Tool approval required")
            if phase == "execution":
                execution_error = str(result.get("error") or "Tool execution failed")
                if execution_error.startswith("LookupError:"):
                    raise LookupError(execution_error.split(":", 1)[1].strip())
            raise RuntimeError(result.get("error") or "Tool execution failed")

        finish_invocation(
            context.invocation_id,
            result={"tool": name, "success": True, "trace_id": context.trace_id},
        )
        return {
            "structuredContent": result.get("data"),
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result.get("data"), ensure_ascii=False),
                }
            ],
            "_meta": {
                "serverInfo": _server_info(),
                "toolResult": result,
                "trace_id": context.trace_id,
                "invocation_id": context.invocation_id,
            },
        }
    except (RuntimeScopeViolation, RuntimeNotFound, RuntimeOperationNotFound) as exc:
        # Persist the violation for operators, but return a stable error that
        # cannot reveal runtime ids, owners, connector names, or credentials.
        trace.add_event("mcp_runtime_access_denied", {"reason": type(exc).__name__})
        trace.record_error("mcp_runtime_dispatch", type(exc).__name__)
        persist_invocation_trace(context.invocation_id, trace.finalize())
        fail_invocation(context.invocation_id, error={"tool": name, "error": "runtime unavailable"})
        raise PermissionError("Runtime scope is unavailable") from exc
    except (LookupError, PermissionError, ValueError, RuntimeError):
        raise
    except Exception as exc:
        trace.record_error("mcp_tool_call", str(exc), exception=exc)
        trace_data = trace.finalize()
        persist_invocation_trace(context.invocation_id, trace_data)
        fail_invocation(context.invocation_id, error={"tool": name, "error": str(exc)})
        raise
