"""Ports of the vector_db unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vector_db._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    embed_texts: Any  # ollama_client.embeddings.embed_texts
    get_store: Any  # storage.records.get_store


_binding: Binding[Host] = Binding("vector_db")
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
    "embed_texts",
    "get_store",
    "provide",
]


def embed_texts(*args: Any, **kwargs: Any) -> Any:
    return current().embed_texts(*args, **kwargs)


def get_store(*args: Any, **kwargs: Any) -> Any:
    return current().get_store(*args, **kwargs)
