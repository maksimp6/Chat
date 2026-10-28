"""Ports of the plugin_system unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from plugin_system._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    UniversalToolCall: Any  # tool_providers.universal_tool_platform.UniversalToolCall
    UniversalToolExecutor: Any  # tool_providers.universal_tool_platform.UniversalToolExecutor


_binding: Binding[Host] = Binding("plugin_system")
provide = _binding.provide
bind = _binding.bind
current = _binding.current
bound = _binding.names()

__all__ = ["Host", "HostNotBound", "bind", "bound", "current", "provide"]
