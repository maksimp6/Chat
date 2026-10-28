"""Ports of the project_board unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from project_board._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    MemoryStore: Any  # memory_engine.MemoryStore
    StoreError: Any  # memory_engine.StoreError


_binding: Binding[Host] = Binding("project_board")
provide = _binding.provide
bind = _binding.bind
current = _binding.current
bound = _binding.names()

__all__ = ["Host", "HostNotBound", "bind", "bound", "current", "provide"]
