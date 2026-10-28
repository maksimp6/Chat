"""Ports of the conversations unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from conversations._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    ConversationOwnershipStore: (
        Any  # agent_memory.conversation_ownership_store.ConversationOwnershipStore
    )
    get_conversations: Any  # storage.db.get_conversations
    get_memory_client: Any  # agent_memory.client.get_memory_client
    memory_file_path: Any  # storage.db.memory_file_path


_binding: Binding[Host] = Binding("conversations")
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
    "get_conversations",
    "get_memory_client",
    "memory_file_path",
]


def get_conversations(*args: Any, **kwargs: Any) -> Any:
    return current().get_conversations(*args, **kwargs)


def get_memory_client(*args: Any, **kwargs: Any) -> Any:
    return current().get_memory_client(*args, **kwargs)


def memory_file_path(*args: Any, **kwargs: Any) -> Any:
    return current().memory_file_path(*args, **kwargs)
