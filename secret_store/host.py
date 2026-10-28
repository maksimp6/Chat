"""Ports of the secret_store unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from secret_store._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    CloudProviderError: Any  # cloud.base.CloudProviderError
    CloudRuSecretManagementClient: (
        Any  # cloud.cloudru.secret_management.CloudRuSecretManagementClient
    )
    get_current_trace: Any  # observability.trace_manager.get_current_trace
    get_store: Any  # storage.records.get_store


_binding: Binding[Host] = Binding("secret_store")
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
    "get_current_trace",
    "get_store",
    "provide",
]


def get_current_trace(*args: Any, **kwargs: Any) -> Any:
    return current().get_current_trace(*args, **kwargs)


def get_store(*args: Any, **kwargs: Any) -> Any:
    return current().get_store(*args, **kwargs)
