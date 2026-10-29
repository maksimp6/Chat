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
    ModelDiscoveryError: Any  # provider_accounts.model_discovery.ModelDiscoveryError
    delete_conversation: Any  # storage.records.delete_conversation
    get_conversations: Any  # storage.records.get_conversations
    get_current_owner_id: Any  # economy.treasury_identity.get_current_owner_id
    get_memory_client: Any  # agent_memory.client.get_memory_client
    memory_file_path: Any  # storage.records.memory_file_path
    update_conversation_model: Any  # storage.records.update_conversation_model
    update_conversation_title: Any  # storage.records.update_conversation_title
    global_settings: Any  # yandex_client_modules.tool_config.global_settings
    validate_dialog_model: (
        Any  # provider_accounts.model_discovery.validate_dialog_model
    )


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
    "delete_conversation",
    "get_conversations",
    "get_current_owner_id",
    "get_memory_client",
    "memory_file_path",
    "update_conversation_model",
    "update_conversation_title",
    "global_settings",
    "validate_dialog_model",
]


def delete_conversation(*args: Any, **kwargs: Any) -> Any:
    return current().delete_conversation(*args, **kwargs)


def get_conversations(*args: Any, **kwargs: Any) -> Any:
    return current().get_conversations(*args, **kwargs)


def get_current_owner_id(*args: Any, **kwargs: Any) -> Any:
    return current().get_current_owner_id(*args, **kwargs)


def get_memory_client(*args: Any, **kwargs: Any) -> Any:
    return current().get_memory_client(*args, **kwargs)


def memory_file_path(*args: Any, **kwargs: Any) -> Any:
    return current().memory_file_path(*args, **kwargs)


def update_conversation_model(*args: Any, **kwargs: Any) -> Any:
    return current().update_conversation_model(*args, **kwargs)


def update_conversation_title(*args: Any, **kwargs: Any) -> Any:
    return current().update_conversation_title(*args, **kwargs)


def global_settings(*args: Any, **kwargs: Any) -> Any:
    return current().global_settings(*args, **kwargs)


def validate_dialog_model(*args: Any, **kwargs: Any) -> Any:
    return current().validate_dialog_model(*args, **kwargs)
