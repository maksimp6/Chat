"""Ports of the runtime unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    ContractViolation: Any  # api.contracts.ContractViolation
    StorageCredentialsMissing: Any  # storage.contracts.StorageCredentialsMissing
    StorageError: Any  # storage.contracts.StorageError
    StorageProviderUnavailable: Any  # storage.contracts.StorageProviderUnavailable
    UniversalToolCall: Any  # tool_providers.universal_tool_platform.UniversalToolCall
    UniversalToolExecutor: (
        Any  # tool_providers.universal_tool_platform.UniversalToolExecutor
    )
    _warn_deprecated_execution_hooks: (
        Any  # tool_providers.universal_tool_platform._warn_deprecated_execution_hooks
    )
    check_access: Any  # conversations.ownership.check_access
    clone_profile: Any  # sessions.profiles.clone_profile
    create_invocation: Any  # invocation.manager.create_invocation
    create_session: Any  # sessions.manager.create_session
    get_current_owner_id: Any  # economy.treasury_identity.get_current_owner_id
    get_invocation_status: Any  # invocation.api.get_invocation_status
    get_invocation_trace: Any  # invocation.api.get_invocation_trace
    get_profile: Any  # sessions.profiles.get_profile
    get_session: Any  # sessions.manager.get_session
    list_profiles: Any  # sessions.profiles.list_profiles
    session_profile: Any  # sessions.profiles.session_profile
    validate_api_contract: Any  # api.contracts.validate_api_contract


_binding: Binding[Host] = Binding("runtime")
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
    "check_access",
    "clone_profile",
    "create_invocation",
    "create_session",
    "get_current_owner_id",
    "get_invocation_status",
    "get_invocation_trace",
    "get_profile",
    "get_session",
    "list_profiles",
    "session_profile",
    "validate_api_contract",
]


def check_access(*args: Any, **kwargs: Any) -> Any:
    return current().check_access(*args, **kwargs)


def clone_profile(*args: Any, **kwargs: Any) -> Any:
    return current().clone_profile(*args, **kwargs)


def create_invocation(*args: Any, **kwargs: Any) -> Any:
    return current().create_invocation(*args, **kwargs)


def create_session(*args: Any, **kwargs: Any) -> Any:
    return current().create_session(*args, **kwargs)


def get_current_owner_id(*args: Any, **kwargs: Any) -> Any:
    return current().get_current_owner_id(*args, **kwargs)


def get_invocation_status(*args: Any, **kwargs: Any) -> Any:
    return current().get_invocation_status(*args, **kwargs)


def get_invocation_trace(*args: Any, **kwargs: Any) -> Any:
    return current().get_invocation_trace(*args, **kwargs)


def get_profile(*args: Any, **kwargs: Any) -> Any:
    return current().get_profile(*args, **kwargs)


def get_session(*args: Any, **kwargs: Any) -> Any:
    return current().get_session(*args, **kwargs)


def list_profiles(*args: Any, **kwargs: Any) -> Any:
    return current().list_profiles(*args, **kwargs)


def session_profile(*args: Any, **kwargs: Any) -> Any:
    return current().session_profile(*args, **kwargs)


def validate_api_contract(*args: Any, **kwargs: Any) -> Any:
    return current().validate_api_contract(*args, **kwargs)
