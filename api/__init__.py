"""API contracts: strict contracts shared by routes, offline validation against the
pinned Yandex documentation snapshot, reviewed outbound request profiles and the
single outbound admission boundary for Yandex API callers.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["contracts", "yandex_transport"]


def __getattr__(name: str) -> Any:
    """Public submodules are imported on first access."""
    if name in __all__:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
