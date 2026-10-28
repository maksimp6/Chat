"""The economy of Logos: a paper treasury ledger with a demo top-up and its HTTP API, the
shared local identity used for accounting, billing and energy-based compute pricing of
execution traces, department budgets, provider pricing snapshots and knowledge
economics.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "billing",
    "budget_controller",
    "compute_resources",
    "knowledge_economics",
    "treasury",
    "treasury_identity",
    "treasury_routes",
]


def __getattr__(name: str) -> Any:
    """Public submodules are imported on first access."""
    if name in __all__:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
