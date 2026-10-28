"""Model-provider accounts: deployment credentials and their API, provider-agnostic key
rotation and the rotation workers' logic, import of runner-issued keys, per-user
quotas and their API, and model discovery with the model catalog API.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "credentials",
    "credentials_routes",
    "key_rotation_cli",
    "model_discovery",
    "model_routes",
    "quota_routes",
    "quotas",
]


def __getattr__(name: str) -> Any:
    """Public submodules are imported on first access."""
    if name in __all__:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
