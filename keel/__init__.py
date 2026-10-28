"""Keel Platform config management and reconciliation."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__version__ = "0.1.0"

__all__ = ["config", "health", "planner", "providers", "reconciler", "recovery"]


def __getattr__(name: str) -> Any:
    """Public submodules are imported on first access."""
    if name in __all__:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
