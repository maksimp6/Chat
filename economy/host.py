"""Ports of the economy unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from economy._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    ALL_MODELS: Any  # core.config.ALL_MODELS
    AUDIO_STT_PRICE_PER_SEC: Any  # core.config.AUDIO_STT_PRICE_PER_SEC
    AUDIO_TTS_PRICE_PER_SEC: Any  # core.config.AUDIO_TTS_PRICE_PER_SEC
    CloudProviderError: Any  # cloud.base.CloudProviderError
    KristallError: Any  # kristall.KristallError
    canonical_model_key: Any  # core.config.canonical_model_key
    get_current_trace: Any  # observability.trace_manager.get_current_trace
    get_store: Any  # storage.records.get_store


_binding: Binding[Host] = Binding("economy")
provide = _binding.provide
bind = _binding.bind
current = _binding.current
bound = _binding.names()

__all__ = [
    "Host",
    "HostNotBound",
    "bind",
    "bound",
    "canonical_model_key",
    "current",
    "get_current_trace",
    "get_store",
    "provide",
]


def canonical_model_key(*args: Any, **kwargs: Any) -> Any:
    return current().canonical_model_key(*args, **kwargs)


def get_current_trace(*args: Any, **kwargs: Any) -> Any:
    return current().get_current_trace(*args, **kwargs)


def get_store(*args: Any, **kwargs: Any) -> Any:
    return current().get_store(*args, **kwargs)
