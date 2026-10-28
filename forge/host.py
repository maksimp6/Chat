"""Ports of the forge unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from forge._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    get_current_owner_id: Any  # economy.treasury_identity.get_current_owner_id
    get_store: Any  # storage.db.get_store


_binding: Binding[Host] = Binding("forge")
provide = _binding.provide
bind = _binding.bind
current = _binding.current
bound = _binding.names()

__all__ = [
    "Host",
    "HostNotBound",
    "bind",
    "bound",
    "current",
    "provide",
    "get_current_owner_id",
    "get_store",
]


def get_current_owner_id(*args: Any, **kwargs: Any) -> Any:
    return current().get_current_owner_id(*args, **kwargs)


def get_store(*args: Any, **kwargs: Any) -> Any:
    return current().get_store(*args, **kwargs)
