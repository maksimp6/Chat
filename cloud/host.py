"""Ports of the cloud unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cloud._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    BudgetLimitExceeded: Any  # economy.budget_controller.BudgetLimitExceeded
    CloudBudgetGuard: Any  # economy.budget_controller.CloudBudgetGuard
    StorageCredentialsMissing: Any  # storage.contracts.StorageCredentialsMissing
    StorageError: Any  # storage.contracts.StorageError
    StorageObject: Any  # storage.contracts.StorageObject
    StorageObjectNotFound: Any  # storage.contracts.StorageObjectNotFound
    StorageProviderUnavailable: Any  # storage.contracts.StorageProviderUnavailable
    _safe_relative_path: Any  # storage.contracts.safe_relative_path
    get_current_trace: Any  # observability.trace_manager.get_current_trace
    secret_storage_ready: Any  # secret_store.key_manager.secret_storage_ready
    ssh_runtime_exec: Any  # tool_providers.runtime_tools.ssh_runtime_exec
    store_secret: Any  # secret_store.key_manager.store_secret


_binding: Binding[Host] = Binding("cloud")
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
    "get_current_trace",
    "secret_storage_ready",
    "ssh_runtime_exec",
    "store_secret",
]


def get_current_trace(*args: Any, **kwargs: Any) -> Any:
    return current().get_current_trace(*args, **kwargs)


def secret_storage_ready(*args: Any, **kwargs: Any) -> Any:
    return current().secret_storage_ready(*args, **kwargs)


def ssh_runtime_exec(*args: Any, **kwargs: Any) -> Any:
    return current().ssh_runtime_exec(*args, **kwargs)


def store_secret(*args: Any, **kwargs: Any) -> Any:
    return current().store_secret(*args, **kwargs)
