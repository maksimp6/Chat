"""Ports of the invocation unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from invocation._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    ExecutionTrace: Any  # observability.trace_manager.ExecutionTrace
    KristallError: Any  # kristall.KristallError
    KristallLimitError: Any  # kristall.KristallLimitError
    MAX_JOURNAL_FRAME_BYTES: Any  # kristall.store.MAX_JOURNAL_FRAME_BYTES
    create_session: Any  # sessions.manager.create_session
    get_memory_client: Any  # agent_memory.client.get_memory_client
    get_messages: Any  # storage.records.get_messages
    memory_file_path: Any  # storage.records.memory_file_path
    restore_session: Any  # sessions.manager.restore_session


_binding: Binding[Host] = Binding("invocation")
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
    "create_session",
    "get_memory_client",
    "get_messages",
    "memory_file_path",
    "restore_session",
]


def create_session(*args: Any, **kwargs: Any) -> Any:
    return current().create_session(*args, **kwargs)


def get_memory_client(*args: Any, **kwargs: Any) -> Any:
    return current().get_memory_client(*args, **kwargs)


def get_messages(*args: Any, **kwargs: Any) -> Any:
    return current().get_messages(*args, **kwargs)


def memory_file_path(*args: Any, **kwargs: Any) -> Any:
    return current().memory_file_path(*args, **kwargs)


def restore_session(*args: Any, **kwargs: Any) -> Any:
    return current().restore_session(*args, **kwargs)
