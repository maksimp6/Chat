from __future__ import annotations

import os
from flask import Blueprint, Response, jsonify, request

from .auth import _require_auth
from .protocol import (
    DEFAULT_PROTOCOL_VERSION,
    MCP_PATH,
    OAUTH_SCOPE,
    PUBLIC_BASE_URL,
    SUPPORTED_PROTOCOL_VERSIONS,
    _error_response,
    _jsonrpc_result,
    _request_protocol_version,
    _server_info,
)
from .runtime_bridge import _handle_call
from .tools import _tools_list

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
                "Content-Type, Authorization, MCP-Protocol-Version, Mcp-Method, Mcp-Name"
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
        params = payload.get("params")
        if not isinstance(params, dict):
            return _error_response(request_id, -32602, "tools/call params must be an object")
        call_meta = params.get("_meta") or {}
        if not isinstance(call_meta, dict):
            return _error_response(request_id, -32602, "tools/call _meta must be an object")
        runtime_id = call_meta.get("alice/runtime_id")
        resource_runtime_id = call_meta.get("alice/resource_runtime_id")
        if runtime_id is not None and (not isinstance(runtime_id, str) or not runtime_id.strip()):
            return _error_response(
                request_id, -32602, "alice/runtime_id must be a non-empty string"
            )
        if resource_runtime_id is not None and (
            not isinstance(resource_runtime_id, str) or not resource_runtime_id.strip()
        ):
            return _error_response(
                request_id, -32602, "alice/resource_runtime_id must be a non-empty string"
            )
        try:
            result = _handle_call(
                str(params.get("name") or ""),
                params.get("arguments", {}),
                user,
                runtime_id=runtime_id,
                resource_runtime_id=resource_runtime_id,
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
