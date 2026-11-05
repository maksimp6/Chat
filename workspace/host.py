"""Ports of the workspace unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from workspace._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    Config: Any  # core.config.Config
    ExpiredCredentialError: Any  # provider_accounts.credentials.ExpiredCredentialError
    NoActiveCredentialError: Any  # provider_accounts.credentials.NoActiveCredentialError
    YandexClientError: Any  # yandex_client_modules.errors.YandexClientError
    YandexResponsesClient: Any  # yandex_client_modules.client.YandexResponsesClient


_binding: Binding[Host] = Binding("workspace")
provide = _binding.provide
bind = _binding.bind
current = _binding.current
bound = _binding.names()

__all__ = ["Host", "HostNotBound", "bind", "bound", "current", "provide"]
