"""Ports of the web_shell unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from web_shell._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    KristallError: Any  # kristall.KristallError
    current_runtime_base_path: Any  # runtime.current_runtime_base_path
    get_current_owner_id: Any  # economy.treasury_identity.get_current_owner_id
    get_donation_url: Any  # core.config.get_donation_url
    get_store: Any  # storage.records.get_store
    memory_file_path: Any  # storage.records.memory_file_path
    problem: Any  # invocation.problems.problem
    storage_initializers: Any  # composition.web.STORAGE_INITIALIZERS


_binding: Binding[Host] = Binding("web_shell")
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
    "current_runtime_base_path",
    "get_current_owner_id",
    "get_donation_url",
    "get_store",
    "memory_file_path",
    "problem",
    "provide",
]


def current_runtime_base_path(*args: Any, **kwargs: Any) -> Any:
    return current().current_runtime_base_path(*args, **kwargs)


def get_current_owner_id(*args: Any, **kwargs: Any) -> Any:
    return current().get_current_owner_id(*args, **kwargs)


def get_donation_url(*args: Any, **kwargs: Any) -> Any:
    return current().get_donation_url(*args, **kwargs)


def get_store(*args: Any, **kwargs: Any) -> Any:
    return current().get_store(*args, **kwargs)


def memory_file_path(*args: Any, **kwargs: Any) -> Any:
    return current().memory_file_path(*args, **kwargs)


def problem(*args: Any, **kwargs: Any) -> Any:
    return current().problem(*args, **kwargs)
