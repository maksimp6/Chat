"""Conversations: ownership records of shared local conversations, helpers for creating
Yandex conversations, and the HTTP API of one conversation (title, model, deletion and
dialog settings).
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["ownership", "routes"]


def __getattr__(name: str) -> Any:
    """Public submodules are imported on first access."""
    if name in __all__:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
