"""Shared local identity for the unauthenticated MCP transport."""

from __future__ import annotations

from mcp_server.host import get_current_owner_id


def _auth_mode() -> str:
    return "anonymous"


def _auth_user_from_request() -> str:
    """Use the same local identity as the web application without checking tokens."""
    return get_current_owner_id()
