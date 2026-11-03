"""Ports of the rag_sync unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rag_sync._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    Config: Any  # core.config.Config
    YandexClientError: Any  # yandex_client_modules.errors.YandexClientError
    YandexResponsesClient: Any  # yandex_client_modules.client.YandexResponsesClient
    get_store: Any  # storage.records.get_store
    knowledge_vector_store_ids: Any  # yandex_client_modules.tool_config.knowledge_vector_store_ids
    vector_add: Any  # vector_db.store.add


_binding: Binding[Host] = Binding("rag_sync")
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
    "get_store",
    "knowledge_vector_store_ids",
    "vector_add",
]


def get_store(*args: Any, **kwargs: Any) -> Any:
    return current().get_store(*args, **kwargs)


def knowledge_vector_store_ids(*args: Any, **kwargs: Any) -> Any:
    return current().knowledge_vector_store_ids(*args, **kwargs)


def vector_add(*args: Any, **kwargs: Any) -> Any:
    return current().vector_add(*args, **kwargs)
