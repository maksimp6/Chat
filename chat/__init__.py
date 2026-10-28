"""The chat: conversation routes that record the full execution chain, and the voice
(Yandex Realtime), image generation (Alice AI ART), embeddings, prompt classification
and synchronous completion endpoints.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "classification_routes",
    "completion_routes",
    "embedding_routes",
    "image_routes",
    "mcp_routes",
    "voice_routes",
]


def __getattr__(name: str) -> Any:
    """Public submodules are imported on first access."""
    if name in __all__:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
