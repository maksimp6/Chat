"""Ports of the model_editor unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from model_editor._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    StorageObjectNotFound: Any  # storage.StorageObjectNotFound
    get_current_owner_id: Any  # economy.treasury_identity.get_current_owner_id
    storage_provider_from_env: Any  # storage.contracts.storage_provider_from_env


_binding: Binding[Host] = Binding("model_editor")
provide = _binding.provide
bind = _binding.bind
current = _binding.current
bound = _binding.names()

__all__ = ["Host", "HostNotBound", "bind", "bound", "current", "provide", "get_current_owner_id", "storage_provider_from_env"]


def get_current_owner_id(*args: Any, **kwargs: Any) -> Any:
    return current().get_current_owner_id(*args, **kwargs)


def storage_provider_from_env(*args: Any, **kwargs: Any) -> Any:
    return current().storage_provider_from_env(*args, **kwargs)
