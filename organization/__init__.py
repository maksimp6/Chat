"""Departments of the Logos organization: the department registry and API, the Government
department (cases and IP registration) and the Partner Relations department (a local
CRM with communications and follow-ups), with their tools.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["departments", "government", "partner_relations"]


def __getattr__(name: str) -> Any:
    """Public submodules are imported on first access."""
    if name in __all__:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
