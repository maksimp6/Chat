"""The workspace HTTP API: the file manager's files kept on the phone, and a bounded project tree."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["file_routes", "project_tree"]


def __getattr__(name: str) -> Any:
    """Public submodules are imported on first access."""
    if name in __all__:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
