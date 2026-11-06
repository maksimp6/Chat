"""Ports of the runtime unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    get_invocation_status: Any  # invocation.api.get_invocation_status
    get_invocation_trace: Any  # invocation.api.get_invocation_trace
    validate_api_contract: Any  # api.contracts.validate_api_contract


_binding: Binding[Host] = Binding("runtime")
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
    "get_invocation_status",
    "get_invocation_trace",
    "provide",
    "validate_api_contract",
]


def get_invocation_status(*args: Any, **kwargs: Any) -> Any:
    return current().get_invocation_status(*args, **kwargs)


def get_invocation_trace(*args: Any, **kwargs: Any) -> Any:
    return current().get_invocation_trace(*args, **kwargs)


def validate_api_contract(*args: Any, **kwargs: Any) -> Any:
    return current().validate_api_contract(*args, **kwargs)
