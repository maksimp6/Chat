"""The Yandex AI Studio client: the Responses API client with its request, polling,
parsing, MCP, conversation and file mixins, the Realtime WebSocket and image-
generation transports, hosted-tool settings, the API-key provider and a minimal SDK.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "api_key_provider",
    "client",
    "errors",
    "image_generation",
    "realtime",
    "request_builder",
    "request_mixin",
    "sdk",
    "tool_config",
]


def __getattr__(name: str) -> Any:
    """Public submodules are imported on first access."""
    if name in __all__:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
