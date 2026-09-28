from __future__ import annotations

import json
import os
from typing import Any, Mapping, Optional

from flask import Response, request

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


def _public_base_url() -> str:
    return os.getenv("ALICE_MCP_PUBLIC_URL", "").rstrip("/") or PUBLIC_BASE_URL


def _sanitize_error_string(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "Traceback (most recent call last):" in text:
        return "Internal error"
    return text.splitlines()[0]


def _sanitize_error_payload(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_error_string(value)
    if isinstance(value, list):
        return [_sanitize_error_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_error_payload(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _sanitize_error_payload(item) for key, item in value.items()}
    return value


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
    error: dict[str, Any] = {"code": code, "message": _sanitize_error_string(message)}
    if data is not None:
        error["data"] = _sanitize_error_payload(data)
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
    params = payload.get("params")
    meta = params.get("_meta") if isinstance(params, Mapping) else None
    if isinstance(meta, Mapping):
        return (
            str(meta.get("io.modelcontextprotocol/protocolVersion") or "").strip()
            or DEFAULT_PROTOCOL_VERSION
        )
    return DEFAULT_PROTOCOL_VERSION
