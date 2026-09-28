"""MCP server package for Alice Pro."""

from .protocol import (
    MCP_PATH,
    SUPPORTED_PROTOCOL_VERSIONS,
    DEFAULT_PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    OAUTH_SCOPE,
    PUBLIC_BASE_URL,
)
from .tools import registry
from .runtime_bridge import mcp_runtime_dispatcher
from .transport import (
    chatgpt_mcp_bp,
    oauth_protected_resource,
    mcp_options,
    mcp_get,
    mcp_post,
)

__all__ = [
    "MCP_PATH",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "DEFAULT_PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "OAUTH_SCOPE",
    "PUBLIC_BASE_URL",
    "registry",
    "mcp_runtime_dispatcher",
    "chatgpt_mcp_bp",
    "oauth_protected_resource",
    "mcp_options",
    "mcp_get",
    "mcp_post",
]
