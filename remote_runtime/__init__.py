"""Remote runtimes: the branch-aware application environment control plane and its REST
API, and the SSH runtime (execution as configured Linux users, its settings policy and
HTTP API).
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["environment_routes", "environments", "ssh", "ssh_routes", "ssh_settings"]


def __getattr__(name: str) -> Any:
    """Public submodules are imported on first access."""
    if name in __all__:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
