"""Which unit provides each port: the one place where units meet.

Generated from the units' ``host.py`` files. ``register_all`` loads ``core/.env``
and registers a factory per unit; a factory imports its providers and builds the
host the first time the unit needs it."""

from __future__ import annotations

from core.env import load_env_file


def _agent_memory():
    from agent_memory import host
    from kristall import Kristall as kristall_Kristall
    from storage.db import memory_file_path as memory_file_path

    return host.Host(
        kristall_Kristall=kristall_Kristall, memory_file_path=memory_file_path
    )


def _cloud():
    from cloud import host
    from economy.budget_controller import BudgetLimitExceeded as BudgetLimitExceeded
    from economy.budget_controller import CloudBudgetGuard as CloudBudgetGuard
    from storage.contracts import StorageCredentialsMissing as StorageCredentialsMissing
    from storage.contracts import StorageError as StorageError
    from storage.contracts import StorageObject as StorageObject
    from storage.contracts import StorageObjectNotFound as StorageObjectNotFound
    from storage.contracts import (
        StorageProviderUnavailable as StorageProviderUnavailable,
    )
    from storage.contracts import safe_relative_path as _safe_relative_path
    from observability.trace_manager import get_current_trace as get_current_trace
    from secret_store.key_manager import secret_storage_ready as secret_storage_ready
    from tool_providers.runtime_tools import ssh_runtime_exec as ssh_runtime_exec
    from secret_store.key_manager import store_secret as store_secret

    return host.Host(
        BudgetLimitExceeded=BudgetLimitExceeded,
        CloudBudgetGuard=CloudBudgetGuard,
        StorageCredentialsMissing=StorageCredentialsMissing,
        StorageError=StorageError,
        StorageObject=StorageObject,
        StorageObjectNotFound=StorageObjectNotFound,
        StorageProviderUnavailable=StorageProviderUnavailable,
        _safe_relative_path=_safe_relative_path,
        get_current_trace=get_current_trace,
        secret_storage_ready=secret_storage_ready,
        ssh_runtime_exec=ssh_runtime_exec,
        store_secret=store_secret,
    )


def _conversations():
    from conversations import host
    from agent_memory.conversation_ownership_store import (
        ConversationOwnershipStore as ConversationOwnershipStore,
    )
    from storage.db import get_conversations as get_conversations
    from agent_memory.client import get_memory_client as get_memory_client
    from storage.db import memory_file_path as memory_file_path

    return host.Host(
        ConversationOwnershipStore=ConversationOwnershipStore,
        get_conversations=get_conversations,
        get_memory_client=get_memory_client,
        memory_file_path=memory_file_path,
    )


def _forge():
    from forge import host
    from economy.treasury_identity import get_current_owner_id as get_current_owner_id
    from storage.db import get_store as get_store

    return host.Host(get_current_owner_id=get_current_owner_id, get_store=get_store)


def _model_editor():
    from model_editor import host
    from storage import StorageObjectNotFound as StorageObjectNotFound
    from economy.treasury_identity import get_current_owner_id as get_current_owner_id
    from storage.contracts import storage_provider_from_env as storage_provider_from_env

    return host.Host(
        StorageObjectNotFound=StorageObjectNotFound,
        get_current_owner_id=get_current_owner_id,
        storage_provider_from_env=storage_provider_from_env,
    )


def _organization():
    from organization import host
    from economy.treasury_identity import TreasuryIdentityError as TreasuryIdentityError
    from sessions.manager import create_session as create_session
    from economy.treasury_identity import get_current_owner_id as get_current_owner_id
    from sessions.manager import get_session as get_session
    from storage.db import get_store as get_store
    from tool_providers.universal_tool_platform import (
        validate_json_schema as validate_json_schema,
    )

    return host.Host(
        TreasuryIdentityError=TreasuryIdentityError,
        create_session=create_session,
        get_current_owner_id=get_current_owner_id,
        get_session=get_session,
        get_store=get_store,
        validate_json_schema=validate_json_schema,
    )


def _plugin_system():
    from plugin_system import host
    from tool_providers.universal_tool_platform import (
        UniversalToolCall as UniversalToolCall,
    )
    from tool_providers.universal_tool_platform import (
        UniversalToolExecutor as UniversalToolExecutor,
    )

    return host.Host(
        UniversalToolCall=UniversalToolCall, UniversalToolExecutor=UniversalToolExecutor
    )


def _project_board():
    from project_board import host
    from memory_engine import MemoryStore as MemoryStore
    from memory_engine import StoreError as StoreError

    return host.Host(MemoryStore=MemoryStore, StoreError=StoreError)


def _secret_store():
    from secret_store import host
    from cloud.base import CloudProviderError as CloudProviderError
    from cloud.cloudru.secret_management import (
        CloudRuSecretManagementClient as CloudRuSecretManagementClient,
    )
    from storage.db import get_store as get_store

    return host.Host(
        CloudProviderError=CloudProviderError,
        CloudRuSecretManagementClient=CloudRuSecretManagementClient,
        get_store=get_store,
    )


def _sessions():
    from sessions import host
    from agent_memory.client import get_memory_client as get_memory_client
    from storage.db import memory_file_path as memory_file_path

    return host.Host(
        get_memory_client=get_memory_client, memory_file_path=memory_file_path
    )


def _storage():
    from storage import host
    from cloud.cloudru.object_storage import (
        CloudRuObjectStorage as CloudRuObjectStorage,
    )
    from kristall import KristallError as KristallError
    from runtime.request_context import (
        current_runtime_data_root as current_runtime_data_root,
    )
    from agent_memory.client import get_memory_client as get_memory_client

    return host.Host(
        CloudRuObjectStorage=CloudRuObjectStorage,
        KristallError=KristallError,
        current_runtime_data_root=current_runtime_data_root,
        get_memory_client=get_memory_client,
    )


def _workspace():
    from workspace import host
    from core.config import Config as Config
    from yandex_client_modules.errors import YandexClientError as YandexClientError
    from yandex_client_modules.client import (
        YandexResponsesClient as YandexResponsesClient,
    )
    from yandex_client_modules.request_mixin import (
        resolve_global_provider_credential as _resolve_global_provider_credential,
    )
    from yandex_client_modules.request_mixin import (
        provider_request_headers as provider_request_headers,
    )

    return host.Host(
        Config=Config,
        YandexClientError=YandexClientError,
        YandexResponsesClient=YandexResponsesClient,
        _resolve_global_provider_credential=_resolve_global_provider_credential,
        provider_request_headers=provider_request_headers,
    )


def register_all() -> None:
    load_env_file()
    from agent_memory import host as agent_memory_host

    agent_memory_host.provide(_agent_memory)
    from cloud import host as cloud_host

    cloud_host.provide(_cloud)
    from conversations import host as conversations_host

    conversations_host.provide(_conversations)
    from forge import host as forge_host

    forge_host.provide(_forge)
    from model_editor import host as model_editor_host

    model_editor_host.provide(_model_editor)
    from organization import host as organization_host

    organization_host.provide(_organization)
    from plugin_system import host as plugin_system_host

    plugin_system_host.provide(_plugin_system)
    from project_board import host as project_board_host

    project_board_host.provide(_project_board)
    from secret_store import host as secret_store_host

    secret_store_host.provide(_secret_store)
    from sessions import host as sessions_host

    sessions_host.provide(_sessions)
    from storage import host as storage_host

    storage_host.provide(_storage)
    from workspace import host as workspace_host

    workspace_host.provide(_workspace)
