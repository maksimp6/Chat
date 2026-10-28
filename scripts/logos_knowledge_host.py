"""Ports of the tools.knowledge unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scripts._logos_knowledge_binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    Config: Any  # core.config.Config
    DatabaseError: Any  # storage.memory_db.DatabaseError
    KristallError: Any  # kristall.KristallError
    LogosClient: Any  # chat.mcp_routes.LogosClient
    UniversalToolCall: Any  # tool_providers.universal_tool_platform.UniversalToolCall
    UniversalToolExecutor: (
        Any  # tool_providers.universal_tool_platform.UniversalToolExecutor
    )
    YandexClientError: Any  # yandex_client_modules.errors.YandexClientError
    _resolve_global_provider_credential: (
        Any  # yandex_client_modules.request_mixin.resolve_global_provider_credential
    )
    create_invocation: Any  # invocation.manager.create_invocation
    create_invocation_trace: Any  # invocation.trace.create_invocation_trace
    fail_invocation: Any  # invocation.manager.fail_invocation
    filesystem: Any  # tool_providers.filesystem
    finish_invocation: Any  # invocation.manager.finish_invocation
    initialize_application: Any  # composition.application.initialize
    persist_invocation_trace: Any  # invocation.manager.persist_invocation_trace
    registry: Any  # tool_providers.registry.registry
    seed_provider_credential: Any  # agent_runtime.issue_runner.seed_provider_credential
    start_invocation: Any  # invocation.manager.start_invocation
    validate_json_schema: (
        Any  # tool_providers.universal_tool_platform.validate_json_schema
    )


_binding: Binding[Host] = Binding("tools.knowledge")
provide = _binding.provide
bind = _binding.bind
current = _binding.current
bound = _binding.names()

__all__ = [
    "Host",
    "HostNotBound",
    "_resolve_global_provider_credential",
    "bind",
    "bound",
    "create_invocation",
    "create_invocation_trace",
    "current",
    "fail_invocation",
    "finish_invocation",
    "initialize_application",
    "persist_invocation_trace",
    "provide",
    "seed_provider_credential",
    "start_invocation",
    "validate_json_schema",
]


def _resolve_global_provider_credential(*args: Any, **kwargs: Any) -> Any:
    return current()._resolve_global_provider_credential(*args, **kwargs)


def create_invocation(*args: Any, **kwargs: Any) -> Any:
    return current().create_invocation(*args, **kwargs)


def create_invocation_trace(*args: Any, **kwargs: Any) -> Any:
    return current().create_invocation_trace(*args, **kwargs)


def fail_invocation(*args: Any, **kwargs: Any) -> Any:
    return current().fail_invocation(*args, **kwargs)


def finish_invocation(*args: Any, **kwargs: Any) -> Any:
    return current().finish_invocation(*args, **kwargs)


def initialize_application(*args: Any, **kwargs: Any) -> Any:
    return current().initialize_application(*args, **kwargs)


def persist_invocation_trace(*args: Any, **kwargs: Any) -> Any:
    return current().persist_invocation_trace(*args, **kwargs)


def seed_provider_credential(*args: Any, **kwargs: Any) -> Any:
    return current().seed_provider_credential(*args, **kwargs)


def start_invocation(*args: Any, **kwargs: Any) -> Any:
    return current().start_invocation(*args, **kwargs)


def validate_json_schema(*args: Any, **kwargs: Any) -> Any:
    return current().validate_json_schema(*args, **kwargs)
