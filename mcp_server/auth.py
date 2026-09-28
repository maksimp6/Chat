from __future__ import annotations

import os
import secrets
from typing import Any, Mapping, Optional

from flask import request

from .protocol import OAUTH_SCOPE, _jsonrpc_error, _public_base_url


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


def _protected_resource_url() -> Optional[str]:
    public_base_url = _public_base_url()
    if not public_base_url:
        return None
    return f"{public_base_url}/.well-known/oauth-protected-resource"


def _www_authenticate() -> Optional[str]:
    resource = _protected_resource_url()
    if resource:
        return "Bearer " + f'resource_metadata="{resource}"'
    return "Bearer"


def _auth_user_from_request() -> Optional[str]:
    """Resolve the authenticated MCP principal."""
    mode = _auth_mode()
    if mode == "anonymous":
        return os.getenv("ALICE_MCP_USER_ID") or None

    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise PermissionError("****** token required")
    token = authorization[7:].strip()
    if not token:
        raise PermissionError("****** token required")

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
    if OAUTH_SCOPE and OAUTH_SCOPE not in scopes:
        raise PermissionError("Access token lacks the required scope")

    return str(data.get("sub") or "") or None


def _require_auth(request_id: Any) -> tuple[Optional[str], Optional[Any]]:
    try:
        return _auth_user_from_request(), None
    except PermissionError as exc:
        return None, _jsonrpc_error(
            request_id,
            -32001,
            str(exc),
            status=401,
            headers={"WWW-Authenticate": _www_authenticate()},
        )
