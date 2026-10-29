"""Ports of the yandex_client_modules unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from yandex_client_modules._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    Config: Any  # core.config.Config
    CredentialCryptoError: Any  # secret_store.credential_crypto.CredentialCryptoError
    EMBEDDING_MODELS: Any  # core.config.EMBEDDING_MODELS
    ExecutionTrace: Any  # observability.trace_manager.ExecutionTrace
    IMAGE_MODELS: Any  # core.config.IMAGE_MODELS
    ProviderQuotaExceeded: Any  # provider_accounts.quotas.ProviderQuotaExceeded
    UniversalToolCall: Any  # tool_providers.universal_tool_platform.UniversalToolCall
    UniversalToolExecutor: (
        Any  # tool_providers.universal_tool_platform.UniversalToolExecutor
    )
    decrypt_secret: Any  # secret_store.credential_crypto.decrypt_secret
    encrypt_secret: Any  # secret_store.credential_crypto.encrypt_secret
    get_controlled_memory_summary: (
        Any  # agent_memory.manager.get_controlled_memory_summary
    )
    get_current_owner_id: Any  # economy.treasury_identity.get_current_owner_id
    get_model_uri: Any  # core.config.get_model_uri
    get_store: Any  # storage.records.get_store
    mcp_storage: Any  # mcp_server.storage
    record_usage: Any  # provider_accounts.quotas.record_usage
    registry: Any  # tool_providers.registry.registry
    release_request: Any  # provider_accounts.quotas.release_request
    reserve_request: Any  # provider_accounts.quotas.reserve_request
    resolve_client_credential: (
        Any  # provider_accounts.credentials.resolve_client_credential
    )
    ultra_profiles: Any  # core.model_profiles.ultra_profiles
    ultra_reasoning_controllable: (
        Any  # core.model_profiles.ultra_reasoning_controllable
    )
    ultra_request_defaults: Any  # core.model_profiles.ultra_request_defaults
    upstream_model_id: Any  # core.config.upstream_model_id


_binding: Binding[Host] = Binding("yandex_client_modules")
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
    "get_controlled_memory_summary",
    "get_current_owner_id",
    "get_model_uri",
    "get_store",
    "record_usage",
    "release_request",
    "reserve_request",
    "resolve_client_credential",
    "ultra_profiles",
    "ultra_reasoning_controllable",
    "ultra_request_defaults",
    "upstream_model_id",
]


def get_controlled_memory_summary(*args: Any, **kwargs: Any) -> Any:
    return current().get_controlled_memory_summary(*args, **kwargs)


def get_current_owner_id(*args: Any, **kwargs: Any) -> Any:
    return current().get_current_owner_id(*args, **kwargs)


def get_model_uri(*args: Any, **kwargs: Any) -> Any:
    return current().get_model_uri(*args, **kwargs)


def get_store(*args: Any, **kwargs: Any) -> Any:
    return current().get_store(*args, **kwargs)


def record_usage(*args: Any, **kwargs: Any) -> Any:
    return current().record_usage(*args, **kwargs)


def release_request(*args: Any, **kwargs: Any) -> Any:
    return current().release_request(*args, **kwargs)


def reserve_request(*args: Any, **kwargs: Any) -> Any:
    return current().reserve_request(*args, **kwargs)


def resolve_client_credential(*args: Any, **kwargs: Any) -> Any:
    return current().resolve_client_credential(*args, **kwargs)


def ultra_profiles(*args: Any, **kwargs: Any) -> Any:
    return current().ultra_profiles(*args, **kwargs)


def ultra_reasoning_controllable(*args: Any, **kwargs: Any) -> Any:
    return current().ultra_reasoning_controllable(*args, **kwargs)


def ultra_request_defaults(*args: Any, **kwargs: Any) -> Any:
    return current().ultra_request_defaults(*args, **kwargs)


def upstream_model_id(*args: Any, **kwargs: Any) -> Any:
    return current().upstream_model_id(*args, **kwargs)
