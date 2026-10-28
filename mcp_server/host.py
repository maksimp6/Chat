"""Ports of the mcp_server unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp_server._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    ALL_MODELS: Any  # core.config.ALL_MODELS
    AgentGateway: Any  # agent_runtime.gateway.AgentGateway
    CredentialCryptoError: Any  # secret_store.credential_crypto.CredentialCryptoError
    RuntimeDispatcher: Any  # runtime.RuntimeDispatcher
    RuntimeNotFound: Any  # runtime.RuntimeNotFound
    RuntimeOperationNotFound: Any  # runtime.RuntimeOperationNotFound
    RuntimeScopeViolation: Any  # runtime.RuntimeScopeViolation
    UniversalToolCall: Any  # tool_providers.universal_tool_platform.UniversalToolCall
    UniversalToolExecutor: (
        Any  # tool_providers.universal_tool_platform.UniversalToolExecutor
    )
    bind_current_trace: Any  # observability.trace_manager.bind_current_trace
    check_access: Any  # conversations.ownership.check_access
    create_invocation: Any  # invocation.manager.create_invocation
    create_invocation_trace: Any  # invocation.trace.create_invocation_trace
    decrypt_secret: Any  # secret_store.credential_crypto.decrypt_secret
    encrypt_secret: Any  # secret_store.credential_crypto.encrypt_secret
    fail_invocation: Any  # invocation.manager.fail_invocation
    finish_invocation: Any  # invocation.manager.finish_invocation
    get_current_owner_id: Any  # economy.treasury_identity.get_current_owner_id
    get_invocation_status: Any  # invocation.api.get_invocation_status
    get_invocation_trace: Any  # invocation.api.get_invocation_trace
    get_messages: Any  # storage.db.get_messages
    get_owned_conversation: Any  # conversations.ownership.get_owned_conversation
    get_session: Any  # sessions.manager.get_session
    get_store: Any  # storage.db.get_store
    grep_search: Any  # tool_providers.filesystem.grep_search
    list_directory: Any  # tool_providers.filesystem.list_directory
    list_owned_conversations: Any  # conversations.ownership.list_owned_conversations
    persist_invocation_trace: Any  # invocation.manager.persist_invocation_trace
    read_file: Any  # tool_providers.filesystem.read_file
    registry: Any  # tool_providers.registry.registry
    reset_current_trace: Any  # observability.trace_manager.reset_current_trace
    start_invocation: Any  # invocation.manager.start_invocation
    tool_call_requires_approval: (
        Any  # tool_providers.universal_tool_platform.tool_call_requires_approval
    )


_binding: Binding[Host] = Binding("mcp_server")
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
    "bind_current_trace",
    "check_access",
    "create_invocation",
    "create_invocation_trace",
    "decrypt_secret",
    "encrypt_secret",
    "fail_invocation",
    "finish_invocation",
    "get_current_owner_id",
    "get_invocation_status",
    "get_invocation_trace",
    "get_messages",
    "get_owned_conversation",
    "get_session",
    "get_store",
    "grep_search",
    "list_directory",
    "list_owned_conversations",
    "persist_invocation_trace",
    "read_file",
    "reset_current_trace",
    "start_invocation",
    "tool_call_requires_approval",
]


def bind_current_trace(*args: Any, **kwargs: Any) -> Any:
    return current().bind_current_trace(*args, **kwargs)


def check_access(*args: Any, **kwargs: Any) -> Any:
    return current().check_access(*args, **kwargs)


def create_invocation(*args: Any, **kwargs: Any) -> Any:
    return current().create_invocation(*args, **kwargs)


def create_invocation_trace(*args: Any, **kwargs: Any) -> Any:
    return current().create_invocation_trace(*args, **kwargs)


def decrypt_secret(*args: Any, **kwargs: Any) -> Any:
    return current().decrypt_secret(*args, **kwargs)


def encrypt_secret(*args: Any, **kwargs: Any) -> Any:
    return current().encrypt_secret(*args, **kwargs)


def fail_invocation(*args: Any, **kwargs: Any) -> Any:
    return current().fail_invocation(*args, **kwargs)


def finish_invocation(*args: Any, **kwargs: Any) -> Any:
    return current().finish_invocation(*args, **kwargs)


def get_current_owner_id(*args: Any, **kwargs: Any) -> Any:
    return current().get_current_owner_id(*args, **kwargs)


def get_invocation_status(*args: Any, **kwargs: Any) -> Any:
    return current().get_invocation_status(*args, **kwargs)


def get_invocation_trace(*args: Any, **kwargs: Any) -> Any:
    return current().get_invocation_trace(*args, **kwargs)


def get_messages(*args: Any, **kwargs: Any) -> Any:
    return current().get_messages(*args, **kwargs)


def get_owned_conversation(*args: Any, **kwargs: Any) -> Any:
    return current().get_owned_conversation(*args, **kwargs)


def get_session(*args: Any, **kwargs: Any) -> Any:
    return current().get_session(*args, **kwargs)


def get_store(*args: Any, **kwargs: Any) -> Any:
    return current().get_store(*args, **kwargs)


def grep_search(*args: Any, **kwargs: Any) -> Any:
    return current().grep_search(*args, **kwargs)


def list_directory(*args: Any, **kwargs: Any) -> Any:
    return current().list_directory(*args, **kwargs)


def list_owned_conversations(*args: Any, **kwargs: Any) -> Any:
    return current().list_owned_conversations(*args, **kwargs)


def persist_invocation_trace(*args: Any, **kwargs: Any) -> Any:
    return current().persist_invocation_trace(*args, **kwargs)


def read_file(*args: Any, **kwargs: Any) -> Any:
    return current().read_file(*args, **kwargs)


def reset_current_trace(*args: Any, **kwargs: Any) -> Any:
    return current().reset_current_trace(*args, **kwargs)


def start_invocation(*args: Any, **kwargs: Any) -> Any:
    return current().start_invocation(*args, **kwargs)


def tool_call_requires_approval(*args: Any, **kwargs: Any) -> Any:
    return current().tool_call_requires_approval(*args, **kwargs)
