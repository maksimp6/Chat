"""Ports of the tools.narration unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scripts._narration_binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    yandex_request: Any  # api.yandex_transport.yandex_request


_binding: Binding[Host] = Binding("tools.narration")
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
    "yandex_request",
]


def yandex_request(*args: Any, **kwargs: Any) -> Any:
    return current().yandex_request(*args, **kwargs)
