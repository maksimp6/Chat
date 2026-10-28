"""Ports of the storage unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from storage._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    CloudRuObjectStorage: Any  # cloud.cloudru.object_storage.CloudRuObjectStorage
    KristallError: Any  # kristall.KristallError
    current_runtime_data_root: Any  # runtime.request_context.current_runtime_data_root
    get_memory_client: Any  # agent_memory.client.get_memory_client


_binding: Binding[Host] = Binding("storage")
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
    "current_runtime_data_root",
    "get_memory_client",
]


def current_runtime_data_root(*args: Any, **kwargs: Any) -> Any:
    return current().current_runtime_data_root(*args, **kwargs)


def get_memory_client(*args: Any, **kwargs: Any) -> Any:
    return current().get_memory_client(*args, **kwargs)
