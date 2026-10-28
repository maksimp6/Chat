"""Ports of the organization unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from organization._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    TreasuryIdentityError: Any  # economy.treasury_identity.TreasuryIdentityError
    create_session: Any  # sessions.manager.create_session
    get_current_owner_id: Any  # economy.treasury_identity.get_current_owner_id
    get_session: Any  # sessions.manager.get_session
    get_store: Any  # storage.records.get_store
    validate_json_schema: (
        Any  # tool_providers.universal_tool_platform.validate_json_schema
    )


_binding: Binding[Host] = Binding("organization")
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
    "create_session",
    "get_current_owner_id",
    "get_session",
    "get_store",
    "validate_json_schema",
]


def create_session(*args: Any, **kwargs: Any) -> Any:
    return current().create_session(*args, **kwargs)


def get_current_owner_id(*args: Any, **kwargs: Any) -> Any:
    return current().get_current_owner_id(*args, **kwargs)


def get_session(*args: Any, **kwargs: Any) -> Any:
    return current().get_session(*args, **kwargs)


def get_store(*args: Any, **kwargs: Any) -> Any:
    return current().get_store(*args, **kwargs)


def validate_json_schema(*args: Any, **kwargs: Any) -> Any:
    return current().validate_json_schema(*args, **kwargs)
