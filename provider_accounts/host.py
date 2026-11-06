"""Ports of the provider_accounts unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any

from provider_accounts._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    CHAT_MODEL: Any  # core.config.CHAT_MODEL
    CLASSIFIER_MODELS: Any  # core.config.CLASSIFIER_MODELS
    CloudRuApiKeyProvider: Any  # cloud.cloudru.api_key_provider.CloudRuApiKeyProvider
    CloudRuIamClient: Any  # cloud.cloudru.iam.CloudRuIamClient
    Config: Any  # core.config.Config
    CredentialCryptoError: Any  # secret_store.credential_crypto.CredentialCryptoError
    EMBEDDING_MODELS: Any  # core.config.EMBEDDING_MODELS
    IMAGE_MODELS: Any  # core.config.IMAGE_MODELS
    TEXT_MODELS: Any  # core.config.TEXT_MODELS
    VOICE_MODELS: Any  # core.config.VOICE_MODELS
    YandexApiKeyProvider: (
        Any  # yandex_client_modules.api_key_provider.YandexApiKeyProvider
    )
    _resolve_global_provider_credential: (
        Any  # yandex_client_modules.request_mixin.resolve_global_provider_credential
    )
    annotate_catalog: Any  # core.model_profiles.annotate_catalog
    config: Any  # core.config.config
    decrypt_secret: Any  # secret_store.credential_crypto.decrypt_secret
    encrypt_secret: Any  # secret_store.credential_crypto.encrypt_secret
    encryption_configured: Any  # secret_store.credential_crypto.encryption_configured
    fingerprint_key: Any  # secret_store.credential_crypto.fingerprint_key
    get_store: Any  # storage.records.get_store
    provider_request_headers: (
        Any  # yandex_client_modules.request_mixin.provider_request_headers
    )
    registry_key_for_upstream: Any  # core.config.registry_key_for_upstream
    traced_operation: Any  # observability.trace_manager.traced_operation


_binding: Binding[Host] = Binding("provider_accounts")
provide = _binding.provide
bind = _binding.bind
current = _binding.current
bound = _binding.names()

__all__ = [
    "Host",
    "HostNotBound",
    "annotate_catalog",
    "bind",
    "bound",
    "current",
    "encryption_configured",
    "fingerprint_key",
    "get_store",
    "provide",
    "provider_request_headers",
    "registry_key_for_upstream",
    "traced_operation",
]


def annotate_catalog(*args: Any, **kwargs: Any) -> Any:
    return current().annotate_catalog(*args, **kwargs)


def encryption_configured(*args: Any, **kwargs: Any) -> Any:
    return current().encryption_configured(*args, **kwargs)


def fingerprint_key(*args: Any, **kwargs: Any) -> Any:
    return current().fingerprint_key(*args, **kwargs)


def get_store(*args: Any, **kwargs: Any) -> Any:
    return current().get_store(*args, **kwargs)


def provider_request_headers(*args: Any, **kwargs: Any) -> Any:
    return current().provider_request_headers(*args, **kwargs)


def registry_key_for_upstream(*args: Any, **kwargs: Any) -> Any:
    return current().registry_key_for_upstream(*args, **kwargs)


def traced_operation(*args: Any, **kwargs: Any) -> Any:
    """Decorator factory applied when the decorated function first runs."""

    def decorate(fn: Any) -> Any:
        resolved = None

        @functools.wraps(fn)
        def call(*inner_args: Any, **inner_kwargs: Any) -> Any:
            nonlocal resolved
            if resolved is None:
                resolved = current().traced_operation(*args, **kwargs)(fn)
            return resolved(*inner_args, **inner_kwargs)

        return call

    return decorate
