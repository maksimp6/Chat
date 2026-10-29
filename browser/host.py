"""Ports of the browser unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``. Only ``browser.guard``, ``browser.files`` and
``browser.http_engine`` (``file://`` addresses) call them: file paths follow the
filesystem tools' rule and every request passes the same link-local guard as
``http_fetch``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from browser._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    resolve_tool_path: Any  # tool_providers.filesystem.resolve_tool_path
    resolves_to_link_local: Any  # tool_providers.filesystem.resolves_to_link_local


_binding: Binding[Host] = Binding("browser")
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
    "resolve_tool_path",
    "resolves_to_link_local",
]


def resolve_tool_path(*args: Any, **kwargs: Any) -> Any:
    return current().resolve_tool_path(*args, **kwargs)


def resolves_to_link_local(*args: Any, **kwargs: Any) -> Any:
    return current().resolves_to_link_local(*args, **kwargs)
