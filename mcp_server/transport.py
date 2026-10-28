"""HTTP transport of the MCP endpoint: OPTIONS, GET and POST on the MCP path."""

from __future__ import annotations
from flask import Blueprint, Response, request

from mcp_server.auth import _auth_user_from_request
from mcp_server.protocol import (
    MCP_PATH,
    SUPPORTED_PROTOCOL_VERSIONS,
    _error_response,
    _jsonrpc_result,
    _request_protocol_version,
    _server_info,
)
from mcp_server.runtime_bridge import ToolOutputError, _handle_call
from mcp_server.tools import _tools_list

chatgpt_mcp_bp = Blueprint("chatgpt_mcp", __name__)


def _client_error_message(exc: Exception, fallback: str) -> str:
    if exc.args and isinstance(exc.args[0], str):
        message = exc.args[0]
    else:
        message = fallback
    if "Traceback (most recent call last):" in message:
        return fallback
    lines = message.splitlines()
    message = lines[0].strip() if lines else ""
    return message or fallback


@chatgpt_mcp_bp.route(MCP_PATH, methods=["OPTIONS"])
def mcp_options() -> Response:
    response = Response(status=204)
    response.headers.update(
        {
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
        "Logos Pro MCP endpoint accepts POST requests only.",
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
    if payload.get("jsonrpc") != "2.0" or not isinstance(method, str) or not method:
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

    user = _auth_user_from_request()

    if method == "initialize":
        return _jsonrpc_result(
            request_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {}},
                "serverInfo": _server_info(),
                "instructions": "Logos MCP exposes audited tools through the Universal Tool Registry/Executor.",
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
            return _error_response(
                request_id, -32602, "tools/call params must be an object"
            )
        call_meta = params.get("_meta") or {}
        if not isinstance(call_meta, dict):
            return _error_response(
                request_id, -32602, "tools/call _meta must be an object"
            )
        runtime_id = call_meta.get("logos/runtime_id")
        resource_runtime_id = call_meta.get("logos/resource_runtime_id")
        if runtime_id is not None and (
            not isinstance(runtime_id, str) or not runtime_id.strip()
        ):
            return _error_response(
                request_id, -32602, "logos/runtime_id must be a non-empty string"
            )
        if resource_runtime_id is not None and (
            not isinstance(resource_runtime_id, str) or not resource_runtime_id.strip()
        ):
            return _error_response(
                request_id,
                -32602,
                "logos/resource_runtime_id must be a non-empty string",
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
            return _error_response(
                request_id,
                -32602,
                _client_error_message(exc, "Requested resource was not found"),
                status=404,
            )
        except PermissionError as exc:
            return _error_response(
                request_id,
                -32003,
                _client_error_message(exc, "Tool authorization denied"),
                status=403,
            )
        except (TypeError, ValueError) as exc:
            return _error_response(
                request_id,
                -32602,
                _client_error_message(exc, "Tool input validation failed"),
            )
        except ToolOutputError as exc:
            # The tool already ran; the ids point to its persisted result and trace.
            return _error_response(
                request_id,
                -32603,
                _client_error_message(exc, "Tool output serialization failed"),
                {"invocation_id": exc.invocation_id, "trace_id": exc.trace_id},
                status=500,
            )
        except Exception:
            return _error_response(
                request_id, -32603, "Tool execution failed", status=500
            )

        return _jsonrpc_result(request_id, result)

    return _error_response(
        request_id, -32601, f"Method not found: {method}", status=404
    )
