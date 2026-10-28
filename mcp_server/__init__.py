"""MCP server package for Logos."""

from mcp_server.protocol import (
    DEFAULT_PROTOCOL_VERSION,
    MCP_PATH,
    OAUTH_SCOPE,
    PUBLIC_BASE_URL,
    SERVER_NAME,
    SERVER_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
)
from mcp_server.transport import (
    chatgpt_mcp_bp,
    mcp_get,
    mcp_options,
    mcp_post,
)

__all__ = [
    "DEFAULT_PROTOCOL_VERSION",
    "MCP_PATH",
    "OAUTH_SCOPE",
    "PUBLIC_BASE_URL",
    "SERVER_NAME",
    "SERVER_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "chatgpt_mcp_bp",
    "mcp_get",
    "mcp_options",
    "mcp_post",
    "mcp_runtime_dispatcher",
    "registry",
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
