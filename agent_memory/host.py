"""Ports of the agent_memory unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_memory._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    kristall_Kristall: Any  # kristall.Kristall
    memory_file_path: Any  # storage.records.memory_file_path


_binding: Binding[Host] = Binding("agent_memory")
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
    "memory_file_path",
    "provide",
]


def memory_file_path(*args: Any, **kwargs: Any) -> Any:
    return current().memory_file_path(*args, **kwargs)
