"""Ports of the observability unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from observability._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    aggregate_billing: Any  # economy.billing.aggregate_billing
    build_ai_billing_item: Any  # economy.billing.build_ai_billing_item
    build_compute_billing_item: Any  # economy.compute_resources.build_compute_billing_item


_binding: Binding[Host] = Binding("observability")
provide = _binding.provide
bind = _binding.bind
current = _binding.current
bound = _binding.names()

__all__ = ["Host", "HostNotBound", "bind", "bound", "current", "provide", "aggregate_billing", "build_ai_billing_item", "build_compute_billing_item"]


def aggregate_billing(*args: Any, **kwargs: Any) -> Any:
    return current().aggregate_billing(*args, **kwargs)


def build_ai_billing_item(*args: Any, **kwargs: Any) -> Any:
    return current().build_ai_billing_item(*args, **kwargs)


def build_compute_billing_item(*args: Any, **kwargs: Any) -> Any:
    return current().build_compute_billing_item(*args, **kwargs)
