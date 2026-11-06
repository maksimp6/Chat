"""MCP server package for Logos."""

from mcp_server.protocol import (
    DEFAULT_PROTOCOL_VERSION,
    MCP_PATH,
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
    "SERVER_NAME",
    "SERVER_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "chatgpt_mcp_bp",
    "mcp_get",
    "mcp_options",
    "mcp_post",
    "registry",
]


def __getattr__(name: str):
    """``registry`` resolves on first use."""
    if name == "registry":
        from mcp_server.host import bound

        return bound.registry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
