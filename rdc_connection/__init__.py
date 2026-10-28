"""Server Remote Desktop Commander pairing integration."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["pairing"]


def __getattr__(name: str) -> Any:
    """Public submodules are imported on first access."""
    if name in __all__:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
