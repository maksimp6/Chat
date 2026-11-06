"""Shared memory of Logos agents on native Kristall: facts and durable records."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["client", "conversation_ownership_store", "extractor", "manager", "routes"]


def __getattr__(name: str) -> Any:
    """Public submodules are imported on first access."""
    if name in __all__:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
