"""ChatGPT Apps SDK / MCP endpoint for Alice Pro.

This module exposes a small, deterministic, read-only MCP surface over
Streamable HTTP.  It deliberately sits beside the existing internal MCP
routes: the existing local/external tool execution path is not exposed
wholesale to ChatGPT.  Only the audited read-only bridge tools below are
available through /mcp.
"""

from __future__ import annotations

import json
import os
import secrets
import uuid
from typing import Any, Callable, Mapping, Optional

from flask import Blueprint, Response, jsonify, request

from agent_gateway import AgentGateway
from invocation_api import get_invocation_status, get_invocation_trace
from invocation_manager import (
    create_invocation,
    start_invocation,
    persist_invocation_trace,
    finish_invocation,
    fail_invocation,
)
from invocation_trace import create_invocation_trace
from session_manager import get_session
from db import get_conversations, get_messages
from conversation_ownership import list_owned_conversations, get_owned_conversation, check_access
from tool_registry import registry
from universal_tool_platform import UniversalToolCall, UniversalToolExecutor
from filesystem_mcp_tools import grep_search, list_directory, read_file


MCP_PATH = "/mcp"
SUPPORTED_PROTOCOL_VERSIONS = {
    "2026-07-28",
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
}
DEFAULT_PROTOCOL_VERSION = "2026-07-28"
SERVER_NAME = "Alice Pro"
SERVER_VERSION = os.getenv("ALICE_VERSION", "dev")
OAUTH_SCOPE = os.getenv("ALICE_MCP_OAUTH_SCOPE", "alice.read")
PUBLIC_BASE_URL = os.getenv("ALICE_MCP_PUBLIC_URL", "").rstrip("/")
OAUTH_ISSUER = os.getenv("ALICE_MCP_OAUTH_ISSUER", "").rstrip("/")
OAUTH_AUTHORIZATION_URL = os.getenv("ALICE_MCP_OAUTH_AUTHORIZATION_URL", "").strip()
OAUTH_TOKEN_URL = os.getenv("ALICE_MCP_OAUTH_TOKEN_URL", "").strip()

MCP_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, GET, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": (
        "Accept, Content-Type, Authorization, MCP-Protocol-Version, "
        "Mcp-Method, Mcp-Name, Mcp-Session-Id, Last-Event-ID"
    ),
    "Access-Control-Expose-Headers": (
        "MCP-Protocol-Version, Mcp-Session-Id, WWW-Authenticate"
    ),
}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _auth_mode() -> str:
    """Return the configured server-side authentication mode.

    Modes:
      - anonymous: intended only for local/developer use;
      - bearer: a single development bearer token;
      - introspection: standards-based OAuth resource-server validation via an
        external OAuth token-introspection endpoint.
    """
    if os.getenv("ALICE_MCP_INTROSPECTION_URL"):
        return "introspection"
    if os.getenv("ALICE_MCP_BEARER_TOKEN"):
        return "bearer"
    if _truthy(os.getenv("ALICE_MCP_ALLOW_ANONYMOUS")):
        return "anonymous"
    return "disabled"


def _server_info() -> dict[str, str]:
    return {"name": SERVER_NAME, "version": SERVER_VERSION}


def _jsonrpc_result(request_id: Any, result: Any) -> Response:
    return Response(
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "result": result},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        status=200,
        mimetype="application/json",
        headers=dict(MCP_CORS_HEADERS),
    )


def _jsonrpc_error(
    request_id: Any,
    code: int,
    message: str,
    data: Any = None,
    *,
    status: int = 400,
    headers: Optional[Mapping[str, str]] = None,
) -> Response:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    response_headers = dict(MCP_CORS_HEADERS)
    response_headers.update(headers or {})
    return Response(
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "error": error},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        status=status,
        mimetype="application/json",
        headers=response_headers,
    )


def _error_response(
    request_id: Any,
    code: int,
    message: str,
    data: Any = None,
    *,
    status: int = 400,
    headers: Optional[Mapping[str, str]] = None,
) -> Response:
    return _jsonrpc_error(
        request_id,
        code,
        message,
        data,
        status=status,
        headers=headers,
    )


def _request_protocol_version(payload: Mapping[str, Any]) -> str:
    header = request.headers.get("MCP-Protocol-Version")
    if header:
        return header.strip()
    meta = payload.get("params", {}).get("_meta", {})
    if isinstance(meta, Mapping):
        return str(meta.get("io.modelcontextprotocol/protocolVersion") or "").strip() or DEFAULT_PROTOCOL_VERSION
    return DEFAULT_PROTOCOL_VERSION


def _public_base_url() -> str:
    return os.getenv("ALICE_MCP_PUBLIC_URL", "").rstrip("/")


def _oauth_issuer() -> str:
    return os.getenv("ALICE_MCP_OAUTH_ISSUER", "").rstrip("/")


def _oauth_authorization_url() -> str:
    return os.getenv("ALICE_MCP_OAUTH_AUTHORIZATION_URL", "").strip()


def _oauth_token_url() -> str:
    return os.getenv("ALICE_MCP_OAUTH_TOKEN_URL", "").strip()


def _oauth_scope() -> str:
    return os.getenv("ALICE_MCP_OAUTH_SCOPE", "alice.read").strip() or "alice.read"


def _protected_resource_url() -> Optional[str]:
    public_base_url = _public_base_url()
    if not public_base_url:
        return None
    return f"{public_base_url}/.well-known/oauth-protected-resource"


def _www_authenticate() -> Optional[str]:
    resource = _protected_resource_url()
    scope = _oauth_scope()
    if resource:
        return f'Bearer resource_metadata="{resource}", scope="{scope}"'
    return f'Bearer scope="{scope}"'


def _auth_user_from_request() -> Optional[str]:
    """Resolve the authenticated MCP principal."""
    mode = _auth_mode()
    if mode == "anonymous":
        # Anonymous MCP has no OAuth/login identity. In the single-user
        # deployment, bind it to the configured server owner so conversation
        # tools can work without inventing a second authentication system.
        return os.getenv("ALICE_MCP_USER_ID") or os.getenv("ALICE_OWNER_ID") or None

    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise PermissionError("Bearer access token required")
    token = authorization[7:].strip()
    if not token:
        raise PermissionError("Bearer access token required")

    if mode == "bearer":
        expected = os.getenv("ALICE_MCP_BEARER_TOKEN", "")
        user_id = os.getenv("ALICE_MCP_USER_ID", "")
        if not expected or not user_id:
            raise PermissionError("MCP bearer authentication is not fully configured")
        if not secrets.compare_digest(token, expected):
            raise PermissionError("Invalid bearer token")
        return user_id

    if mode == "introspection":
        return _introspect_token(token)

    raise PermissionError("MCP authentication is not configured")


def _introspect_token(token: str) -> Optional[str]:
    """Validate an OAuth access token using RFC 7662-style introspection."""
    import requests

    url = os.getenv("ALICE_MCP_INTROSPECTION_URL", "")
    if not url:
        raise PermissionError("OAuth introspection is not configured")

    auth_user = os.getenv("ALICE_MCP_INTROSPECTION_CLIENT_ID", "")
    auth_password = os.getenv("ALICE_MCP_INTROSPECTION_CLIENT_SECRET", "")
    timeout = float(os.getenv("ALICE_MCP_INTROSPECTION_TIMEOUT", "5"))

    kwargs: dict[str, Any] = {
        "data": {"token": token},
        "timeout": timeout,
        "headers": {"Accept": "application/json"},
    }
    if auth_user:
        kwargs["auth"] = (auth_user, auth_password)

    response = requests.post(url, **kwargs)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, Mapping) or not data.get("active"):
        raise PermissionError("Access token is inactive")

    scopes = str(data.get("scope") or "").split()
    scope = _oauth_scope()\n    if scope and scope not in scopes:
        raise PermissionError("Access token lacks the required scope")

    return str(data.get("sub") or "") or None


def _require_auth(request_id: Any) -> tuple[Optional[str], Optional[Response]]:
    try:
        return _auth_user_from_request(), None
    except PermissionError as exc:
        response = _jsonrpc_error(
            request_id,
            -32001,
            str(exc),
            status=401,
            headers={"WWW-Authenticate": _www_authenticate()},
        )
        response.headers["Access-Control-Expose-Headers"] = "WWW-Authenticate, MCP-Protocol-Version"
        return None, response


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
    owner_id = _owner_id_from_invocation(invocation)
    if owner_id and authenticated_user and owner_id == authenticated_user:
        return
    if owner_id is None and _auth_mode() == "anonymous":
        return
    if owner_id is None and authenticated_user:
        raise PermissionError("Invocation has no trusted user ownership")
    if owner_id != authenticated_user:
        raise PermissionError("Invocation is not accessible to this user")


def _tool(
    name: str,
    title: str,
    description: str,
    input_schema: dict[str, Any],
    handler: Callable[[dict[str, Any], Optional[str]], dict[str, Any]],
) -> tuple[dict[str, Any], Callable[[dict[str, Any], Optional[str]], dict[str, Any]]]:
    meta = {
        "openai/toolInvocation/invoking": f"{title}…",
        "openai/toolInvocation/invoked": f"{title}: готово",
    }
    descriptor = {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": input_schema,
        "securitySchemes": [],
        "_meta": meta,
    }
    return descriptor, handler


def _system_status(_arguments: dict[str, Any], _user: Optional[str]) -> dict[str, Any]:
    from config import ALL_MODELS

    categories = registry.get_available_categories()
    return {
        "status": "ok",
        "server": _server_info(),
        "mcp": {
            "endpoint": MCP_PATH,
            "transport": "streamable-http",
            "protocol_versions": sorted(SUPPORTED_PROTOCOL_VERSIONS, reverse=True),
            "auth_mode": _auth_mode(),
        },
        "models": {
            "count": len(ALL_MODELS),
            "available": sorted(ALL_MODELS.keys()),
        },
        "local_tools": {
            "category_count": len(categories),
            "tool_count": sum(len(names) for names in categories.values()),
        },
    }


def _agents(_arguments: dict[str, Any], _user: Optional[str]) -> dict[str, Any]:
    gateway = AgentGateway()
    agents = []
    for agent in gateway.list_agents():
        agents.append(
            {
                "agent_id": agent.agent_id,
                "name": agent.name,
                "capabilities": list(agent.capabilities),
                "version": agent.version,
                "transport": agent.transport,
                "enabled": agent.enabled,
                "metadata": dict(agent.metadata),
            }
        )
    return {"agents": agents}


def _session(arguments: dict[str, Any], _user: Optional[str]) -> dict[str, Any]:
    session_id = str(arguments.get("session_id") or "").strip()
    session = get_session(session_id) if session_id else None
    if not session:
        raise LookupError("Session not found")
    return {
        "session": {
            "id": session["id"],
            "status": session["status"],
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
            "completed_at": session.get("completed_at"),
        }
    }


def _invocation(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    invocation_id = str(arguments.get("invocation_id") or "").strip()
    status = get_invocation_status(invocation_id) if invocation_id else None
    if not status:
        raise LookupError("Invocation not found")
    _authorize_invocation(status, user)
    return {"invocation": status}


def _trace(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    invocation_id = str(arguments.get("invocation_id") or "").strip()
    status = get_invocation_status(invocation_id) if invocation_id else None
    if not status:
        raise LookupError("Invocation not found")
    _authorize_invocation(status, user)
    trace = get_invocation_trace(invocation_id)
    if trace is None:
        raise LookupError("Invocation trace not found")
    return {"invocation_id": invocation_id, "trace": trace}


def _bridge_wrapper(handler):
    def wrapped(arguments: dict[str, Any], cfg: Optional[dict[str, Any]] = None):
        context = (cfg or {}).get("_universal_context") or {}
        return handler(arguments, context.get("user_id"))
    return wrapped


def _require_conversation_user(user: Optional[str]) -> str:
    if not user:
        raise PermissionError("Authenticated user identity is required")
    return str(user)


def _conversations(_arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    owner = _require_conversation_user(user)
    return {"conversations": list_owned_conversations(owner)}


def _conversation(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    owner = _require_conversation_user(user)
    conversation_id = str(arguments.get("conversation_id") or "").strip()
    if not conversation_id:
        raise ValueError("conversation_id is required")
    conversation = get_owned_conversation(conversation_id, owner)
    if conversation is None:
        raise LookupError("Conversation not found")
    return {"conversation": conversation}


def _conversation_messages(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    owner = _require_conversation_user(user)
    conversation_id = str(arguments.get("conversation_id") or "").strip()
    if not conversation_id:
        raise ValueError("conversation_id is required")
    if not check_access(conversation_id, owner):
        raise LookupError("Conversation not found")
    return {
        "conversation_id": conversation_id,
        "messages": get_messages(conversation_id),
    }


def _execution(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    owner = _require_conversation_user(user)
    result = _invocation(arguments, owner)
    return {"execution": result["invocation"]}


def _execution_trace(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    owner = _require_conversation_user(user)
    return _trace(arguments, owner)




def _project_list(args: dict, _user: Optional[str] = None) -> dict:
    return list_directory({"path": args.get("path") or "."})


def _project_read(args: dict, _user: Optional[str] = None) -> dict:
    path = str(args.get("path") or "").strip()
    if not path:
        raise ValueError("path is required")
    result = read_file({
        "path": path,
        "offset": args.get("offset", 0),
        "length": min(int(args.get("length", 65536)), 65536),
    })
    if result.get("error"):
        raise ValueError(result["error"])
    return result


def _project_search(args: dict, _user: Optional[str] = None) -> dict:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    return grep_search({
        "query": query,
        "file_pattern": args.get("file_pattern") or "*.py",
        "max_matches": min(int(args.get("max_matches", 50)), 50),
    })


def _register_project_read_tools() -> None:
    tools = [
        (
            "alice_list_project_files",
            "List project files",
            "List files and directories inside the Alice Pro project.",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": [],
                "additionalProperties": False,
            },
            _project_list,
        ),
        (
            "alice_read_project_file",
            "Read project file",
            "Read a bounded UTF-8 file from the Alice Pro project.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "offset": {"type": "integer", "minimum": 0},
                    "length": {"type": "integer", "minimum": 1, "maximum": 65536},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            _project_read,
        ),
        (
            "alice_search_project",
            "Search project code",
            "Search text inside the Alice Pro project.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "file_pattern": {"type": "string"},
                    "max_matches": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            _project_search,
        ),
    ]
    for name, title, description, input_schema, handler in tools:
        registry.register(
            "chatgpt_project",
            name,
            {
                "title": title,
                "description": description,
                "inputSchema": input_schema,
                "outputSchema": {"type": "object"},
                "capabilities": ["project", "mcp"],
                "risk_level": "medium",
                "read_only": True,
                "requires_approval": False,
                "supported_transports": ["mcp"],
                "executor": {"type": "local"},
                "func": _bridge_wrapper(handler),
            },
        )


_register_project_read_tools()

def _register_conversation_tools() -> None:
    tools = [
        (
            "alice_list_conversations",
            "List conversations",
            "List conversations owned by the authenticated Alice Pro user.",
            {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            _conversations,
        ),
        (
            "alice_get_conversation",
            "Get conversation",
            "Read one conversation owned by the authenticated Alice Pro user.",
            {
                "type": "object",
                "properties": {"conversation_id": {"type": "string", "minLength": 1}},
                "required": ["conversation_id"],
                "additionalProperties": False,
            },
            _conversation,
        ),
        (
            "alice_get_conversation_messages",
            "Get conversation messages",
            "Read messages and persisted ExecutionTrace data for one owned conversation.",
            {
                "type": "object",
                "properties": {"conversation_id": {"type": "string", "minLength": 1}},
                "required": ["conversation_id"],
                "additionalProperties": False,
            },
            _conversation_messages,
        ),
        (
            "alice_get_execution",
            "Get execution",
            "Read one authenticated execution through the user-facing execution abstraction.",
            {
                "type": "object",
                "properties": {"invocation_id": {"type": "string", "minLength": 1}},
                "required": ["invocation_id"],
                "additionalProperties": False,
            },
            _execution,
        ),
        (
            "alice_get_execution_trace",
            "Get execution trace",
            "Read sanitized ExecutionTrace for one authenticated execution.",
            {
                "type": "object",
                "properties": {"invocation_id": {"type": "string", "minLength": 1}},
                "required": ["invocation_id"],
                "additionalProperties": False,
            },
            _execution_trace,
        ),
    ]
    for name, title, description, input_schema, handler in tools:
        registry.register(
            "chatgpt_conversation",
            name,
            {
                "title": title,
                "description": description,
                "inputSchema": input_schema,
                "outputSchema": {"type": "object"},
                "capabilities": ["conversation", "execution", "mcp"],
                "risk_level": "low",
                "read_only": True,
                "requires_approval": False,
                "supported_transports": ["mcp"],
                "executor": {"type": "local"},
                "func": _bridge_wrapper(handler),
            },
        )


_register_conversation_tools()

def _register_bridge_tools() -> None:
    bridge_tools = [
        (
            "alice_get_system_status",
            "System status",
            "Read-only Alice Pro runtime, MCP, model and local-tool status.",
            {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            _system_status,
        ),
        (
            "alice_list_agents",
            "List agents",
            "List AI agents currently registered in the Alice Pro Agent Gateway.",
            {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            _agents,
        ),
        (
            "alice_get_session",
            "Get session",
            "Read-only lifecycle status for one Alice Pro runtime session.",
            {
                "type": "object",
                "properties": {"session_id": {"type": "string", "minLength": 1}},
                "required": ["session_id"],
                "additionalProperties": False,
            },
            _session,
        ),
        (
            "alice_get_invocation",
            "Get invocation",
            "Read-only status and safe metadata for one Alice Pro invocation.",
            {
                "type": "object",
                "properties": {"invocation_id": {"type": "string", "minLength": 1}},
                "required": ["invocation_id"],
                "additionalProperties": False,
            },
            _invocation,
        ),
        (
            "alice_get_invocation_trace",
            "Get invocation trace",
            "Read-only persisted ExecutionTrace for one Alice Pro invocation.",
            {
                "type": "object",
                "properties": {"invocation_id": {"type": "string", "minLength": 1}},
                "required": ["invocation_id"],
                "additionalProperties": False,
            },
            _trace,
        ),
    ]
    for name, title, description, input_schema, handler in bridge_tools:
        registry.register(
            "chatgpt_bridge",
            name,
            {
                "title": title,
                "description": description,
                "inputSchema": input_schema,
                "outputSchema": {"type": "object"},
                "capabilities": ["read", "mcp"],
                "risk_level": "low",
                "read_only": True,
                "requires_approval": False,
                "supported_transports": ["mcp"],
                "executor": {"type": "local"},
                "func": _bridge_wrapper(handler),
            },
        )


_register_bridge_tools()


def _security_schemes() -> list[dict[str, Any]]:
    mode = _auth_mode()
    if mode == "anonymous":
        return [{"type": "noauth"}]
    return [{"type": "oauth2", "scopes": [_oauth_scope()]}]


def _tools_list() -> list[dict[str, Any]]:
    schemes = _security_schemes()
    result = []
    for definition in registry.get_universal_definitions("mcp"):
        item = {
            "name": definition["name"],
            "title": definition.get("title") or definition["name"],
            "description": definition.get("description", ""),
            "inputSchema": definition.get("inputSchema") or definition.get("input_schema") or {},
            "outputSchema": definition.get("outputSchema") or definition.get("output_schema") or {"type": "object"},
            "securitySchemes": schemes,
            "annotations": {
                "readOnlyHint": bool(definition.get("read_only")),
                "destructiveHint": bool(not definition.get("read_only")),
                "openWorldHint": False,
            },
        }
        meta = dict(definition.get("metadata") or {})
        meta.update({
            "risk_level": definition.get("risk_level"),
            "read_only": definition.get("read_only"),
            "requires_approval": definition.get("requires_approval"),
            "capabilities": definition.get("capabilities") or [],
            "openai/toolInvocation/invoking": f"{item['title']}…",
            "openai/toolInvocation/invoked": f"{item['title']}: готово",
            "securitySchemes": schemes,
        })
        item["_meta"] = meta
        result.append(item)
    return result


def _handle_call(name: str, arguments: Any, user: Optional[str]) -> dict[str, Any]:
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
    trace.set_request({
        "transport": "mcp",
        "method": "tools/call",
        "tool": name,
        "arguments": arguments,
        "call_id": correlation_id,
    })

    call = UniversalToolCall(
        tool_name=name,
        arguments=arguments,
        transport="mcp",
        call_id=correlation_id,
        trace_id=context.trace_id,
        invocation_id=context.invocation_id,
        user_id=user,
        metadata={"source": "chatgpt_mcp"},
    )

    try:
        result = UniversalToolExecutor(registry).execute_with_trace(call, trace)
        trace.add_event("mcp_tool_call_completed", {
            "tool": name,
            "call_id": correlation_id,
            "success": bool(result.get("success")),
        })
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
    except (LookupError, PermissionError, ValueError, RuntimeError):
        raise
    except Exception as exc:
        trace.record_error("mcp_tool_call", str(exc), exception=exc)
        trace_data = trace.finalize()
        persist_invocation_trace(context.invocation_id, trace_data)
        fail_invocation(context.invocation_id, error={"tool": name, "error": str(exc)})
        raise


chatgpt_mcp_bp = Blueprint("chatgpt_mcp", __name__)


@chatgpt_mcp_bp.route("/.well-known/oauth-protected-resource", methods=["GET"])
def oauth_protected_resource() -> Response:
    resource = _public_base_url() or request.url_root.rstrip("/")
    issuer = _oauth_issuer()
    scope = os.getenv("ALICE_MCP_OAUTH_SCOPE", "alice.read")
    body = {
        "resource": resource,
        "authorization_servers": [issuer] if issuer else [],
        "scopes_supported": [scope],
        "resource_documentation": f"{resource}/docs/mcp/chatgpt_apps.md",
    }
    return jsonify({key: value for key, value in body.items() if value not in (None, [], "")})


@chatgpt_mcp_bp.route("/.well-known/oauth-authorization-server", methods=["GET"])
def oauth_authorization_server() -> Response:
    issuer = _oauth_issuer()
    if not issuer:
        return jsonify({"error": "oauth_not_configured"}), 404

    body = {
        "issuer": issuer,
        "authorization_endpoint": _oauth_authorization_url(),
        "token_endpoint": _oauth_token_url(),
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "private_key_jwt"],
        "scopes_supported": [_oauth_scope()],
        "client_id_metadata_document_supported": True,
    }
    return jsonify({key: value for key, value in body.items() if value not in (None, [], "")})


@chatgpt_mcp_bp.route(MCP_PATH, methods=["OPTIONS"])
def mcp_options() -> Response:
    return Response(status=204, headers=dict(MCP_CORS_HEADERS))


def _accepts_sse() -> bool:
    accept = request.headers.get("Accept", "")
    if not accept:
        return True
    return "text/event-stream" in accept or "*/*" in accept


@chatgpt_mcp_bp.route(MCP_PATH, methods=["GET"])
def mcp_get() -> Response:
    _, auth_error = _require_auth(None)
    if auth_error is not None:
        return auth_error
    if not _accepts_sse():
        return Response(
            "GET /mcp requires an Accept header containing text/event-stream.",
            status=406,
            headers={**MCP_CORS_HEADERS, "Allow": "GET, POST, OPTIONS, DELETE"},
            mimetype="text/plain",
        )

    # Alice Pro currently has no server-initiated notifications to stream.
    # Emit a valid SSE comment and close the stateless stream immediately.
    return Response(
        ": alice-pro-mcp\n\n",
        status=200,
        headers={
            **MCP_CORS_HEADERS,
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
        mimetype="text/event-stream",
    )


@chatgpt_mcp_bp.route(MCP_PATH, methods=["DELETE"])
def mcp_delete() -> Response:
    _, auth_error = _require_auth(None)
    if auth_error is not None:
        return auth_error
    # The Alice Pro endpoint is stateless and does not allocate MCP session IDs.
    return Response(status=204, headers=dict(MCP_CORS_HEADERS))


@chatgpt_mcp_bp.route(MCP_PATH, methods=["POST"])
def mcp_post() -> Response:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error_response(None, -32600, "JSON-RPC request must be an object")

    request_id = payload.get("id")
    method = payload.get("method")
    if payload.get("jsonrpc") != "2.0" or not method:
        return _error_response(request_id, -32600, "Invalid JSON-RPC request")

    if method.startswith("notifications/"):
        return Response(status=202, headers=dict(MCP_CORS_HEADERS))

    protocol_version = _request_protocol_version(payload)
    if protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
        return _error_response(
            request_id,
            -32001,
            "Unsupported MCP protocol version",
            {
                "requested": protocol_version or None,
                "supported": sorted(SUPPORTED_PROTOCOL_VERSIONS, reverse=True),
            },
            status=400,
        )

    user, auth_error = _require_auth(request_id)
    if auth_error is not None:
        return auth_error

    if method == "initialize":
        return _jsonrpc_result(
            request_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {}},
                "serverInfo": _server_info(),
                "instructions": "Alice Pro MCP exposes audited tools through the Universal Tool Registry/Executor.",
            },
        )

    if method == "ping":
        return _jsonrpc_result(
            request_id,
            {
                "serverInfo": _server_info(),
                "protocolVersion": protocol_version,
            },
        )

    if method == "server/discover":
        return _jsonrpc_result(
            request_id,
            {
                "serverInfo": _server_info(),
                "capabilities": {"tools": {}},
                "protocolVersion": protocol_version,
            },
        )

    if method == "tools/list":
        return _jsonrpc_result(
            request_id,
            {
                "tools": _tools_list(),
                "ttlMs": 300_000,
                "cacheScope": "private",
                "_meta": {"serverInfo": _server_info()},
            },
        )

    if method == "tools/call":
        params = payload.get("params") or {}
        try:
            result = _handle_call(
                str(params.get("name") or ""),
                params.get("arguments") or {},
                user,
            )
        except LookupError as exc:
            return _error_response(request_id, -32602, str(exc), status=404)
        except PermissionError as exc:
            response = _error_response(
                request_id,
                -32003,
                str(exc),
                data={"_meta": {"mcp/www_authenticate": [_www_authenticate()]}},
                status=401 if "token" in str(exc).lower() else 403,
                headers={"WWW-Authenticate": _www_authenticate()},
            )
            response.headers["Access-Control-Expose-Headers"] = "WWW-Authenticate, MCP-Protocol-Version"
            return response
        except (TypeError, ValueError) as exc:
            return _error_response(request_id, -32602, str(exc))
        except Exception:
            return _error_response(request_id, -32603, "Tool execution failed", status=500)

        return _jsonrpc_result(request_id, result)

    return _error_response(request_id, -32601, f"Method not found: {method}", status=404)
