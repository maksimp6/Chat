"""Ports of the printing3d unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from printing3d._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    printing_store: Any  # forge.binding.printing_store


_binding: Binding[Host] = Binding("printing3d")
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
    "printing_store",
    "provide",
]


def printing_store(*args: Any, **kwargs: Any) -> Any:
    return current().printing_store(*args, **kwargs)
