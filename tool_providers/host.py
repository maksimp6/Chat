"""Ports of the tool_providers unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tool_providers._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    KristallError: Any  # kristall.KristallError
    SSHRuntimeError: Any  # remote_runtime.ssh.SSHRuntimeError
    assert_operation_allowed: (
        Any  # remote_runtime.ssh_settings.assert_operation_allowed
    )
    build_runtime: Any  # remote_runtime.ssh_settings.build_runtime
    enqueue_local_tool_job: Any  # agent_runtime.local_gateway.enqueue_local_tool_job
    external_tool_sources: Any  # composition.tool_sources.SOURCES
    memory_file_path: Any  # storage.db.memory_file_path
    wait_for_local_tool_job: Any  # agent_runtime.local_gateway.wait_for_local_tool_job


_binding: Binding[Host] = Binding("tool_providers")
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
    "assert_operation_allowed",
    "build_runtime",
    "enqueue_local_tool_job",
    "memory_file_path",
    "wait_for_local_tool_job",
]


def assert_operation_allowed(*args: Any, **kwargs: Any) -> Any:
    return current().assert_operation_allowed(*args, **kwargs)


def build_runtime(*args: Any, **kwargs: Any) -> Any:
    return current().build_runtime(*args, **kwargs)


def enqueue_local_tool_job(*args: Any, **kwargs: Any) -> Any:
    return current().enqueue_local_tool_job(*args, **kwargs)


def memory_file_path(*args: Any, **kwargs: Any) -> Any:
    return current().memory_file_path(*args, **kwargs)


def wait_for_local_tool_job(*args: Any, **kwargs: Any) -> Any:
    return current().wait_for_local_tool_job(*args, **kwargs)
