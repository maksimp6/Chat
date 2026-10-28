"""The agent runtime: the provider-agnostic agent gateway, the persistent gateway for
remote Local Tool Agents and its client, the headless GitHub issue runner, the launch
hold, the read-only review policy and helpers for Yandex Responses tool loops.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["cli", "gateway", "issue_runner", "local_gateway", "logos_launch_hold"]


def __getattr__(name: str) -> Any:
    """Public submodules are imported on first access."""
    if name in __all__:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
