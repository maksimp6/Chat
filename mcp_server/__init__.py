"""MCP server package for Logos."""

from mcp_server.protocol import (
    MCP_PATH,
    SUPPORTED_PROTOCOL_VERSIONS,
    DEFAULT_PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    OAUTH_SCOPE,
    PUBLIC_BASE_URL,
)
from mcp_server.transport import (
    chatgpt_mcp_bp,
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
    "mcp_options",
    "mcp_get",
    "mcp_post",
]


def __getattr__(name: str):
    """``registry`` and ``mcp_runtime_dispatcher`` resolve on first use."""
    if name == "registry":
        from mcp_server.host import bound

        return bound.registry
    if name == "mcp_runtime_dispatcher":
        from mcp_server import runtime_bridge

        return runtime_bridge.runtime_dispatcher()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
