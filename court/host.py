"""Ports of the court unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from court._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    TreasuryIdentityError: Any  # economy.treasury_identity.TreasuryIdentityError
    add_message: Any  # storage.records.add_message
    check_access: Any  # conversations.ownership.check_access
    get_conversation_title: Any  # storage.records.get_conversation_title
    get_current_owner_id: Any  # economy.treasury_identity.get_current_owner_id
    get_messages: Any  # storage.records.get_messages
    get_store: Any  # storage.records.get_store
    list_owned_conversations: Any  # conversations.ownership.list_owned_conversations


_binding: Binding[Host] = Binding("court")
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
    "add_message",
    "check_access",
    "get_conversation_title",
    "get_current_owner_id",
    "get_messages",
    "get_store",
    "list_owned_conversations",
]


def add_message(*args: Any, **kwargs: Any) -> Any:
    return current().add_message(*args, **kwargs)


def check_access(*args: Any, **kwargs: Any) -> Any:
    return current().check_access(*args, **kwargs)


def get_conversation_title(*args: Any, **kwargs: Any) -> Any:
    return current().get_conversation_title(*args, **kwargs)


def get_current_owner_id(*args: Any, **kwargs: Any) -> Any:
    return current().get_current_owner_id(*args, **kwargs)


def get_messages(*args: Any, **kwargs: Any) -> Any:
    return current().get_messages(*args, **kwargs)


def get_store(*args: Any, **kwargs: Any) -> Any:
    return current().get_store(*args, **kwargs)


def list_owned_conversations(*args: Any, **kwargs: Any) -> Any:
    return current().list_owned_conversations(*args, **kwargs)
