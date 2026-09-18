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

from flask import Blueprint, Response, jsonify, request

from agent_gateway import AgentGateway
from invocation_api import get_invocation_status, get_invocation_trace
from session_manager import get_session
from tool_registry import registry


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
        return str(meta.get("io.modelcontextprotocol/protocolVersion") or "").strip()
    return ""


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
    """Authenticate the current request.

    External OAuth deployments should use token introspection.  A single
    configured bearer token remains available for private/dev deployments,
    while anonymous access must be explicitly opted into.
    """
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
        headers = {}
        challenge = _www_authenticate()
        if challenge:
            headers["WWW-Authenticate"] = challenge
        return None, _error_response(
            request_id,
            -32001,
            str(exc),
            status=401,
            headers=headers,
        )
    except Exception:
        return None, _error_response(
            request_id,
            -32001,
            "Authentication service unavailable",
            status=503,
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


_TOOL_DEFINITIONS = [
    _tool(
        "alice_get_system_status",
        "System status",
        "Read-only Alice Pro runtime, MCP, model and local-tool status.",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        _system_status,
    ),
    _tool(
        "alice_list_agents",
        "List agents",
        "List AI agents currently registered in the Alice Pro Agent Gateway.",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        _agents,
    ),
    _tool(
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
    _tool(
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
    _tool(
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

_TOOLS = {descriptor["name"]: (descriptor, handler) for descriptor, handler in _TOOL_DEFINITIONS}


def _security_schemes() -> list[dict[str, Any]]:
    mode = _auth_mode()
    if mode == "anonymous":
        return [{"type": "noauth"}]
    if mode == "introspection":
        return [{"type": "oauth2", "scopes": [OAUTH_SCOPE]}]
    # A fixed bearer token is intentionally not advertised as OAuth. ChatGPT
    # OAuth clients cannot be configured with arbitrary customer API keys.
    return []


def _tools_list() -> list[dict[str, Any]]:
    schemes = _security_schemes()
    result = []
    for descriptor, _handler in sorted(_TOOLS.values(), key=lambda item: item[0]["name"]):
        item = dict(descriptor)
        item["securitySchemes"] = schemes
        meta = dict(item.get("_meta") or {})
        meta["securitySchemes"] = schemes
        item["_meta"] = meta
        result.append(item)
    return result


def _handle_call(name: str, arguments: Any, user: Optional[str]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    pair = _TOOLS.get(name)
    if pair is None:
        raise LookupError(f"Unknown tool: {name}")
    descriptor, handler = pair
    result = handler(arguments, user)
    if not isinstance(result, dict):
        result = {"value": result}
    return {
        "structuredContent": result,
        "content": [
            {
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False),
            }
        ],
        "_meta": {"serverInfo": _server_info()},
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
