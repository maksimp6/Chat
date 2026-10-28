"""The tool platform: provider-agnostic tool contracts and the execution pipeline, the
shared tool registry, and the built-in tool sources (git, Termux, system, filesystem,
Wikipedia, profiler, theme, SSH runtime) with JSON Schema helpers and the read-only
policy.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["filesystem", "registry", "runtime_tools", "universal_tool_platform"]


def __getattr__(name: str) -> Any:
    """Public submodules are imported on first access."""
    if name in __all__:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
