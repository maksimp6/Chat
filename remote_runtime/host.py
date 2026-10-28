"""Ports of the remote_runtime unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from remote_runtime._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    ExecutionTrace: Any  # observability.trace_manager.ExecutionTrace
    RuntimeDispatcher: Any  # runtime.RuntimeDispatcher
    RuntimeLoader: Any  # runtime.RuntimeLoader
    RuntimeNotFound: Any  # runtime.RuntimeNotFound
    bind_runtime_request: Any  # runtime.request_context.bind_runtime_request
    close_memory_clients: Any  # agent_memory.client.close_memory_clients
    create_invocation_trace: Any  # invocation.trace.create_invocation_trace
    get_config: Any  # storage.db.get_config
    get_current_owner_id: Any  # economy.treasury_identity.get_current_owner_id
    get_current_trace: Any  # observability.trace_manager.get_current_trace
    get_store: Any  # storage.db.get_store
    init_runtime_db: Any  # storage.db.init_db
    set_config: Any  # storage.db.set_config


_binding: Binding[Host] = Binding("remote_runtime")
provide = _binding.provide
bind = _binding.bind
current = _binding.current
bound = _binding.names()

__all__ = ["Host", "HostNotBound", "bind", "bound", "current", "provide", "bind_runtime_request", "close_memory_clients", "create_invocation_trace", "get_config", "get_current_owner_id", "get_current_trace", "get_store", "init_runtime_db", "set_config"]


def bind_runtime_request(*args: Any, **kwargs: Any) -> Any:
    return current().bind_runtime_request(*args, **kwargs)


def close_memory_clients(*args: Any, **kwargs: Any) -> Any:
    return current().close_memory_clients(*args, **kwargs)


def create_invocation_trace(*args: Any, **kwargs: Any) -> Any:
    return current().create_invocation_trace(*args, **kwargs)


def get_config(*args: Any, **kwargs: Any) -> Any:
    return current().get_config(*args, **kwargs)


def get_current_owner_id(*args: Any, **kwargs: Any) -> Any:
    return current().get_current_owner_id(*args, **kwargs)


def get_current_trace(*args: Any, **kwargs: Any) -> Any:
    return current().get_current_trace(*args, **kwargs)


def get_store(*args: Any, **kwargs: Any) -> Any:
    return current().get_store(*args, **kwargs)


def init_runtime_db(*args: Any, **kwargs: Any) -> Any:
    return current().init_runtime_db(*args, **kwargs)


def set_config(*args: Any, **kwargs: Any) -> Any:
    return current().set_config(*args, **kwargs)
