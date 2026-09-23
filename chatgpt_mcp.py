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
from typing import Any, Callable, Mapping, Optional
import uuid

from flask import Blueprint, Response, jsonify, request

from agent_gateway import AgentGateway
from invocation_api import get_invocation_status, get_invocation_trace
from invocation_manager import (
    create_invocation,
    start_invocation,
    finish_invocation,
    fail_invocation,
    persist_invocation_trace,
)
from invocation_trace import create_invocation_trace
from db import (
    get_conn,
    get_conversations,
    get_messages,
    create_conversation,
    add_message,
)
from config import Config, calculate_full_cost
from session_manager import get_session
from tool_registry import registry
from universal_tool_platform import UniversalToolCall, UniversalToolExecutor
from filesystem_mcp_tools import grep_search, list_directory, read_file


MCP_PATH = "/mcp"
SUPPORTED_PROTOCOL_VERSIONS = {
    "2026-07-28",
    "2025-06-18",
    "2025-03-26",
}
DEFAULT_PROTOCOL_VERSION = "2026-07-28"
SERVER_NAME = "Alice Pro"
SERVER_VERSION = os.getenv("ALICE_VERSION", "dev")
OAUTH_SCOPE = os.getenv("ALICE_MCP_OAUTH_SCOPE", "alice.read")
PUBLIC_BASE_URL = os.getenv("ALICE_MCP_PUBLIC_URL", "").rstrip("/")


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
    return Response(
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "error": error},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        status=status,
        mimetype="application/json",
        headers=dict(headers or {}),
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


def _protected_resource_url() -> Optional[str]:
    if not PUBLIC_BASE_URL:
        return None
    return f"{PUBLIC_BASE_URL}/.well-known/oauth-protected-resource"


def _www_authenticate() -> Optional[str]:
    resource = _protected_resource_url()
    if resource:
        return f'Bearer resource_metadata="{resource}"'
    return "Bearer"


def _auth_user_from_request() -> Optional[str]:
    """Resolve the authenticated MCP user from the configured auth mode."""
    mode = _auth_mode()
    if mode == "anonymous":
        return os.getenv("ALICE_MCP_USER_ID") or None

    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise PermissionError("Bearer access token required")
    token = authorization[7:].strip()
    if not token:
        raise PermissionError("Bearer access token required")

    if mode == "bearer":
        expected = os.getenv("ALICE_MCP_BEARER_TOKEN", "")
        if expected and token == expected:
            return os.getenv("ALICE_MCP_USER_ID") or None
        raise PermissionError("Invalid bearer token")

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
    if OAUTH_SCOPE and OAUTH_SCOPE not in scopes:
        raise PermissionError("Access token lacks the required scope")

    return str(data.get("sub") or "") or None


def _require_auth(request_id: Any) -> tuple[Optional[str], Optional[Response]]:
    try:
        return _auth_user_from_request(), None
    except PermissionError as exc:
        return None, _error_response(
            request_id,
            -32001,
            str(exc),
            status=401,
            headers={"WWW-Authenticate": _www_authenticate()},
        )


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


def _ensure_conversation_control_schema() -> None:
    conn = get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_mcp_owners (
                conversation_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversation_mcp_owners_user "
            "ON conversation_mcp_owners(user_id)"
        )
        conn.commit()
    finally:
        conn.close()


def _claim_conversation(conversation_id: str, user: Optional[str]) -> None:
    _ensure_conversation_control_schema()
    if not user:
        return
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO conversation_mcp_owners (conversation_id, user_id)
            VALUES (?, ?)
            ON CONFLICT(conversation_id) DO NOTHING
            """,
            (conversation_id, user),
        )
        conn.commit()
    finally:
        conn.close()


def _conversation_visible(conversation_id: str, user: Optional[str]) -> bool:
    _ensure_conversation_control_schema()
    if not user and _auth_mode() == "anonymous":
        return True
    if not user:
        return False
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT user_id FROM conversation_mcp_owners WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    finally:
        conn.close()
    return bool(row and str(row["user_id"]) == str(user))


def _conversation(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    conversation_id = str(arguments.get("conversation_id") or "").strip()
    if not conversation_id:
        raise ValueError("conversation_id is required")
    if not _conversation_visible(conversation_id, user):
        raise PermissionError("Conversation is not accessible to this user")

    conversations = [item for item in get_conversations() if item["id"] == conversation_id]
    if not conversations:
        raise LookupError("Conversation not found")
    return {"conversation": conversations[0]}


def _conversation_messages(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    conversation_id = str(arguments.get("conversation_id") or "").strip()
    if not conversation_id:
        raise ValueError("conversation_id is required")
    if not _conversation_visible(conversation_id, user):
        raise PermissionError("Conversation is not accessible to this user")
    return {"conversation_id": conversation_id, "messages": get_messages(conversation_id)}


def _conversations(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    _ensure_conversation_control_schema()
    rows = get_conversations()
    if user:
        conn = get_conn()
        try:
            owned = {
                str(row["conversation_id"])
                for row in conn.execute(
                    "SELECT conversation_id FROM conversation_mcp_owners WHERE user_id = ?",
                    (user,),
                ).fetchall()
            }
        finally:
            conn.close()
        rows = [row for row in rows if row["id"] in owned]
    elif _auth_mode() != "anonymous":
        rows = []
    return {"conversations": rows}


def _create_mcp_conversation(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    title = str(arguments.get("title") or "Новый чат").strip() or "Новый чат"
    model = str(arguments.get("model") or "aliceai-llm").strip() or "aliceai-llm"

    conversation_id = str(uuid.uuid4())
    try:
        from mcp_routes import AliceClient
        remote = AliceClient(Config).create_conversation()
        conversation_id = str(remote.get("id") or conversation_id)
    except Exception:
        # Keep local chat creation available even when the provider is temporarily unavailable.
        pass

    create_conversation(conversation_id, title, model)
    _claim_conversation(conversation_id, user)
    return {
        "conversation": {
            "id": conversation_id,
            "title": title,
            "model": model,
        }
    }


def _send_message(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    conversation_id = str(arguments.get("conversation_id") or "").strip()
    message = str(arguments.get("message") or "").strip()
    model = str(arguments.get("model") or "").strip()
    params = arguments.get("params") or {}

    if not conversation_id:
        raise ValueError("conversation_id is required")
    if not message:
        raise ValueError("message is required")
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    if not _conversation_visible(conversation_id, user):
        raise PermissionError("Conversation is not accessible to this user")

    conversations = [item for item in get_conversations() if item["id"] == conversation_id]
    if not conversations:
        raise LookupError("Conversation not found")
    model_key = model or conversations[0]["model"] or "aliceai-llm"

    add_message(conversation_id, "user", message)
    invocation = create_invocation(
        conversation_id,
        conversation_id,
        metadata={"source": "chatgpt_mcp"},
        user_id=user,
    )
    trace = create_invocation_trace(invocation)
    start_invocation(invocation.invocation_id)

    try:
        from mcp_routes import AliceClient
        client = AliceClient(Config)
        response = client.ask_with_mcp(
            message,
            model_key,
            conversation_id,
            params,
            trace=trace,
        )
        reply = client.extract_text(response) or ""
        usage = client.extract_usage(response)
        cost = calculate_full_cost(model_key, usage) if usage else 0.0

        trace_data = trace.finalize()
        persist_invocation_trace(invocation.invocation_id, trace_data)
        add_message(
            conversation_id,
            "assistant",
            reply,
            cost=cost,
            trace=trace_data,
        )
        finish_invocation(
            invocation.invocation_id,
            result={"reply": reply, "usage": usage, "cost": cost},
        )
        return {
            "reply": reply,
            "usage": usage,
            "cost": cost,
            "conversation_id": conversation_id,
            "execution_id": invocation.invocation_id,
            "trace_id": invocation.trace_id,
            "trace": trace_data,
        }
    except Exception as exc:
        trace.record_error("chatgpt_mcp", str(exc), exception=exc)
        trace_data = trace.finalize()
        persist_invocation_trace(invocation.invocation_id, trace_data)
        fail_invocation(invocation.invocation_id, error={"message": str(exc)})
        raise RuntimeError(str(exc)) from exc


def _execution(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    invocation_id = str(arguments.get("execution_id") or "").strip()
    if not invocation_id:
        raise ValueError("execution_id is required")
    status = get_invocation_status(invocation_id)
    if not status:
        raise LookupError("Execution not found")
    _authorize_invocation(status, user)
    status["id"] = status.pop("id", invocation_id)
    return {"execution": status}


def _execution_trace(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    invocation_id = str(arguments.get("execution_id") or "").strip()
    if not invocation_id:
        raise ValueError("execution_id is required")
    status = get_invocation_status(invocation_id)
    if not status:
        raise LookupError("Execution not found")
    _authorize_invocation(status, user)
    trace = get_invocation_trace(invocation_id)
    if trace is None:
        raise LookupError("Execution trace not found")
    return {"execution_id": invocation_id, "trace": trace}


def _session(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    session_id = str(arguments.get("session_id") or "").strip()
    if not session_id:
        raise ValueError("session_id is required")
    session = get_session(session_id)
    if not session:
        raise LookupError("Session not found")

    if user and _auth_mode() != "anonymous":
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT metadata_json FROM invocations "
                "WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()

        owner = None
        if row:
            try:
                owner = json.loads(row["metadata_json"] or "{}").get("user_id")
            except (TypeError, ValueError):
                owner = None
        if str(owner or "") != str(user):
            raise PermissionError("Session is not accessible to this user")

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
            "alice_list_conversations",
            "List conversations",
            "List Alice Pro conversations owned by the authenticated MCP user.",
            {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            _conversations,
        ),
        (
            "alice_get_conversation",
            "Get conversation",
            "Read one Alice Pro conversation owned by the authenticated MCP user.",
            {
                "type": "object",
                "properties": {"conversation_id": {"type": "string", "minLength": 1}},
                "required": ["conversation_id"],
                "additionalProperties": False,
            },
            _conversation,
        ),
        (
            "alice_get_messages",
            "Get messages",
            "Read persisted messages for an owned Alice Pro conversation.",
            {
                "type": "object",
                "properties": {"conversation_id": {"type": "string", "minLength": 1}},
                "required": ["conversation_id"],
                "additionalProperties": False,
            },
            _conversation_messages,
        ),
        (
            "alice_create_conversation",
            "Create conversation",
            "Create a new Alice Pro conversation and bind it to the authenticated MCP user.",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "model": {"type": "string"},
                },
                "required": [],
                "additionalProperties": False,
            },
            _create_mcp_conversation,
        ),
        (
            "alice_send_message",
            "Send message",
            "Send a message through the normal Alice Pro chat/tool pipeline and return its execution correlation IDs.",
            {
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "string", "minLength": 1},
                    "message": {"type": "string", "minLength": 1},
                    "model": {"type": "string"},
                    "params": {"type": "object"},
                },
                "required": ["conversation_id", "message"],
                "additionalProperties": False,
            },
            _send_message,
        ),
        (
            "alice_get_execution",
            "Get execution",
            "Read-only status for the execution created by an Alice Pro conversation request.",
            {
                "type": "object",
                "properties": {"execution_id": {"type": "string", "minLength": 1}},
                "required": ["execution_id"],
                "additionalProperties": False,
            },
            _execution,
        ),
        (
            "alice_get_execution_trace",
            "Get execution trace",
            "Read-only ExecutionTrace for an Alice Pro conversation execution.",
            {
                "type": "object",
                "properties": {"execution_id": {"type": "string", "minLength": 1}},
                "required": ["execution_id"],
                "additionalProperties": False,
            },
            _execution_trace,
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
                "capabilities": ["control-plane", "mcp"],
                "risk_level": "medium" if name == "alice_send_message" else "low",
                "read_only": name not in {"alice_create_conversation", "alice_send_message"},
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
    if mode in {"bearer", "introspection"}:
        return [{"type": "oauth2", "scopes": [OAUTH_SCOPE]}]
    return [{"type": "oauth2", "scopes": [OAUTH_SCOPE]}]


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
    call = UniversalToolCall(
        tool_name=name,
        arguments=arguments,
        transport="mcp",
        user_id=user,
        metadata={"source": "chatgpt_mcp"},
    )
    result = UniversalToolExecutor(registry).execute(call)
    if not result.get("success"):
        phase = (result.get("metadata") or {}).get("phase")
        if phase == "transport":
            raise LookupError(result.get("error") or "Tool transport is not supported")
        if phase == "validation":
            raise ValueError(result.get("error") or "Tool input validation failed")
        if phase == "authorization":
            raise PermissionError(result.get("error") or "Tool authorization denied")
        if phase == "approval_required":
            raise PermissionError(result.get("error") or "Tool approval required")
        raise RuntimeError(result.get("error") or "Tool execution failed")
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
        },
    }


def _validate_headers(payload: Mapping[str, Any]) -> Optional[str]:
    method = payload.get("method")
    body_method = str(method or "")
    header_method = request.headers.get("Mcp-Method", "")
    if not header_method or header_method != body_method:
        return "Mcp-Method header must match the JSON-RPC method"

    if body_method == "tools/call":
        expected = str((payload.get("params") or {}).get("name") or "")
        header_name = request.headers.get("Mcp-Name", "")
        if not header_name or header_name != expected:
            return "Mcp-Name header must match params.name"
    return None


chatgpt_mcp_bp = Blueprint("chatgpt_mcp", __name__)


@chatgpt_mcp_bp.route("/.well-known/oauth-protected-resource", methods=["GET"])
def oauth_protected_resource() -> Response:
    issuer = os.getenv("ALICE_MCP_OAUTH_ISSUER", "").rstrip("/")
    resource = PUBLIC_BASE_URL or request.url_root.rstrip("/")
    body = {
        "resource": resource,
        "authorization_servers": [issuer] if issuer else [],
        "scopes_supported": [OAUTH_SCOPE],
    }
    return jsonify(body)


@chatgpt_mcp_bp.route(MCP_PATH, methods=["OPTIONS"])
def mcp_options() -> Response:
    response = Response(status=204)
    response.headers.update(
        {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": (
                "Content-Type, Authorization, MCP-Protocol-Version, "
                "Mcp-Method, Mcp-Name"
            ),
        }
    )
    return response


@chatgpt_mcp_bp.route(MCP_PATH, methods=["GET"])
def mcp_get() -> Response:
    return Response(
        "Alice Pro MCP endpoint accepts POST requests only.",
        status=405,
        headers={"Allow": "POST, OPTIONS"},
        mimetype="text/plain",
    )


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
        return Response(status=204)

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

    header_error = _validate_headers(payload)
    if header_error:
        return _error_response(request_id, -32600, header_error)

    user, auth_error = _require_auth(request_id)
    if auth_error is not None:
        return auth_error

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
            return _error_response(request_id, -32003, str(exc), status=403)
        except (TypeError, ValueError) as exc:
            return _error_response(request_id, -32602, str(exc))
        except Exception:
            return _error_response(request_id, -32603, "Tool execution failed", status=500)

        return _jsonrpc_result(request_id, result)

    return _error_response(request_id, -32601, f"Method not found: {method}", status=404)
