"""Ports of the agent_runtime unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime._binding import Binding, HostNotBound


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
    bootstrap_credential: Any  # provider_accounts.credentials.bootstrap_credential
    create_invocation: Any  # invocation.manager.create_invocation
    create_invocation_trace: Any  # invocation.trace.create_invocation_trace
    current_runtime_data_root: Any  # runtime.request_context.current_runtime_data_root
    current_runtime_id: Any  # runtime.request_context.current_runtime_id
    decrypt_secret: Any  # secret_store.credential_crypto.decrypt_secret
    encrypt_secret: Any  # secret_store.credential_crypto.encrypt_secret
    env_file_only: Any  # core.env.env_file_only
    fail_invocation: Any  # invocation.manager.fail_invocation
    filesystem: Any  # tool_providers.filesystem
    finish_invocation: Any  # invocation.manager.finish_invocation
    get_store: Any  # storage.records.get_store
    initialize_application: Any  # composition.application.initialize
    persist_invocation_trace: Any  # invocation.manager.persist_invocation_trace
    registry: Any  # tool_providers.registry.registry
    start_invocation: Any  # invocation.manager.start_invocation


_binding: Binding[Host] = Binding("agent_runtime")
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
    "bootstrap_credential",
    "create_invocation",
    "create_invocation_trace",
    "current_runtime_data_root",
    "current_runtime_id",
    "fail_invocation",
    "finish_invocation",
    "get_store",
    "initialize_application",
    "persist_invocation_trace",
    "start_invocation",
]


def bootstrap_credential(*args: Any, **kwargs: Any) -> Any:
    return current().bootstrap_credential(*args, **kwargs)


def create_invocation(*args: Any, **kwargs: Any) -> Any:
    return current().create_invocation(*args, **kwargs)


def create_invocation_trace(*args: Any, **kwargs: Any) -> Any:
    return current().create_invocation_trace(*args, **kwargs)


def current_runtime_data_root(*args: Any, **kwargs: Any) -> Any:
    return current().current_runtime_data_root(*args, **kwargs)


def current_runtime_id(*args: Any, **kwargs: Any) -> Any:
    return current().current_runtime_id(*args, **kwargs)


def env_file_only(*args: Any, **kwargs: Any) -> Any:
    return current().env_file_only(*args, **kwargs)


def fail_invocation(*args: Any, **kwargs: Any) -> Any:
    return current().fail_invocation(*args, **kwargs)


def finish_invocation(*args: Any, **kwargs: Any) -> Any:
    return current().finish_invocation(*args, **kwargs)


def get_store(*args: Any, **kwargs: Any) -> Any:
    return current().get_store(*args, **kwargs)


def initialize_application(*args: Any, **kwargs: Any) -> Any:
    return current().initialize_application(*args, **kwargs)


def persist_invocation_trace(*args: Any, **kwargs: Any) -> Any:
    return current().persist_invocation_trace(*args, **kwargs)


def start_invocation(*args: Any, **kwargs: Any) -> Any:
    return current().start_invocation(*args, **kwargs)
