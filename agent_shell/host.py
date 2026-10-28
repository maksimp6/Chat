"""Ports of the agent_shell unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_shell._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    DEFAULT_MODEL: Any  # agent_runtime.issue_runner.DEFAULT_MODEL
    KristallError: Any  # kristall.KristallError
    get_memory_client: Any  # agent_memory.client.get_memory_client
    run_issue_task: Any  # agent_runtime.issue_runner.run_issue_task


_binding: Binding[Host] = Binding("agent_shell")
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
    "get_memory_client",
]


def get_memory_client(*args: Any, **kwargs: Any) -> Any:
    return current().get_memory_client(*args, **kwargs)
