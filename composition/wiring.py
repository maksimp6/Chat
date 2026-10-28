"""Which unit provides each port: the one place where units meet.

Generated from the units' ``host.py`` files. ``register_all`` loads ``core/.env``
and registers a factory per unit; a factory imports its providers and builds the
host the first time the unit needs it."""

from __future__ import annotations

from importlib import import_module

from core.env import load_env_file


def _agent_memory():
    from agent_memory import host
    from kristall import Kristall as kristall_Kristall
    from storage.db import memory_file_path as memory_file_path

    return host.Host(
        kristall_Kristall=kristall_Kristall, memory_file_path=memory_file_path
    )


def _agent_runtime():
    from agent_runtime import host
    from core.config import Config as Config
    from storage.memory_db import DatabaseError as DatabaseError
    from chat.mcp_routes import LogosClient as LogosClient
    from tool_providers.universal_tool_platform import (
        UniversalToolCall as UniversalToolCall,
    )
    from tool_providers.universal_tool_platform import (
        UniversalToolExecutor as UniversalToolExecutor,
    )
    from provider_accounts.credentials import (
        bootstrap_credential as bootstrap_credential,
    )
    from invocation.manager import create_invocation as create_invocation
    from invocation.trace import create_invocation_trace as create_invocation_trace
    from runtime.request_context import (
        current_runtime_data_root as current_runtime_data_root,
    )
    from runtime.request_context import current_runtime_id as current_runtime_id
    from secret_store.credential_crypto import decrypt_secret as decrypt_secret
    from secret_store.credential_crypto import encrypt_secret as encrypt_secret
    from invocation.manager import fail_invocation as fail_invocation
    import tool_providers.filesystem as filesystem
    from invocation.manager import finish_invocation as finish_invocation
    from storage.db import get_store as get_store
    from composition.application import initialize as initialize_application
    from invocation.manager import persist_invocation_trace as persist_invocation_trace
    from tool_providers.registry import registry as registry
    from invocation.manager import start_invocation as start_invocation

    return host.Host(
        Config=Config,
        DatabaseError=DatabaseError,
        LogosClient=LogosClient,
        UniversalToolCall=UniversalToolCall,
        UniversalToolExecutor=UniversalToolExecutor,
        bootstrap_credential=bootstrap_credential,
        create_invocation=create_invocation,
        create_invocation_trace=create_invocation_trace,
        current_runtime_data_root=current_runtime_data_root,
        current_runtime_id=current_runtime_id,
        decrypt_secret=decrypt_secret,
        encrypt_secret=encrypt_secret,
        fail_invocation=fail_invocation,
        filesystem=filesystem,
        finish_invocation=finish_invocation,
        get_store=get_store,
        initialize_application=initialize_application,
        persist_invocation_trace=persist_invocation_trace,
        registry=registry,
        start_invocation=start_invocation,
    )


def _agent_shell():
    from agent_shell import host
    from agent_runtime.issue_runner import DEFAULT_MODEL as DEFAULT_MODEL
    from kristall import KristallError as KristallError
    from agent_memory.client import get_memory_client as get_memory_client
    from agent_runtime.issue_runner import run_issue_task as run_issue_task

    return host.Host(
        DEFAULT_MODEL=DEFAULT_MODEL,
        KristallError=KristallError,
        get_memory_client=get_memory_client,
        run_issue_task=run_issue_task,
    )


def _chat():
    from chat import host
    from core.config import CLASSIFIER_MODELS as CLASSIFIER_MODELS
    from core.config import Config as Config
    from core.config import EMBEDDING_MODELS as EMBEDDING_MODELS
    from observability.trace_manager import ExecutionTrace as ExecutionTrace
    from core.config import IMAGE_MODELS as IMAGE_MODELS
    from yandex_client_modules.image_generation import IMAGE_SIZES as IMAGE_SIZES
    from yandex_client_modules.image_generation import (
        ImageGenerationError as ImageGenerationError,
    )
    from economy.knowledge_economics import KnowledgeCostConfig as KnowledgeCostConfig
    from yandex_client_modules.image_generation import (
        MAX_PROMPT_CHARS as MAX_PROMPT_CHARS,
    )
    from yandex_client_modules.realtime import MODEL_UPSTREAMS as MODEL_UPSTREAMS
    from provider_accounts.model_discovery import (
        ModelDiscoveryError as ModelDiscoveryError,
    )
    from yandex_client_modules.realtime import OUTPUT_RATE as OUTPUT_RATE
    from provider_accounts.quotas import ProviderQuotaExceeded as ProviderQuotaExceeded
    from yandex_client_modules.image_generation import (
        REQUEST_TIMEOUT_SECONDS as REQUEST_TIMEOUT_SECONDS,
    )
    from yandex_client_modules.realtime import RealtimeError as RealtimeError
    from yandex_client_modules.realtime import RealtimeResult as RealtimeResult
    from agent_skills.registry import SkillRegistry as SkillRegistry
    from agent_skills.registry import SkillRegistryError as SkillRegistryError
    from core.config import TEXT_MODELS as TEXT_MODELS
    from yandex_client_modules.realtime import TTS_MODEL as TTS_MODEL
    from tool_providers.universal_tool_platform import (
        UniversalToolCall as UniversalToolCall,
    )
    from tool_providers.universal_tool_platform import (
        UniversalToolExecutor as UniversalToolExecutor,
    )
    from core.config import VOICE_MODELS as VOICE_MODELS
    from yandex_client_modules.client import (
        YandexResponsesClient as YandexResponsesClient,
    )
    from yandex_client_modules.request_mixin import (
        record_quota_usage as _record_quota_usage,
    )
    from yandex_client_modules.request_mixin import (
        resolve_global_provider_credential as _resolve_global_provider_credential,
    )
    from yandex_client_modules.request_mixin import (
        resolve_quota_user_id as _resolve_quota_user_id,
    )
    from yandex_client_modules.request_builder import response_input as _response_input
    from yandex_client_modules.request_mixin import (
        transport_timeout as _transport_timeout,
    )
    from storage.db import add_message as add_message
    from economy.billing import aggregate_billing as aggregate_billing
    from observability.trace_manager import bind_current_trace as bind_current_trace
    from economy.billing import build_ai_billing_item as build_ai_billing_item
    from yandex_client_modules.image_generation import (
        build_generation_payload as build_generation_payload,
    )
    from economy.knowledge_economics import (
        calculate_knowledge_cost as calculate_knowledge_cost,
    )
    from conversations.ownership import check_access as check_access
    from core.model_profiles import completion_model_keys as completion_model_keys
    from agent_skills.registry import (
        compose_skill_instructions as compose_skill_instructions,
    )
    from storage.db import create_conversation as create_conversation
    from invocation.manager import create_invocation as create_invocation
    from invocation.trace import create_invocation_trace as create_invocation_trace
    from invocation.manager import fail_invocation as fail_invocation
    from invocation.manager import finish_invocation as finish_invocation
    from yandex_client_modules.realtime import generate_realtime as generate_realtime
    from storage.db import get_conv_settings as get_conv_settings
    from storage.db import get_conversation_title as get_conversation_title
    from economy.treasury_identity import get_current_owner_id as get_current_owner_id
    from observability.trace_manager import get_current_trace as get_current_trace
    from storage.db import get_message as get_message
    from storage.db import get_messages as get_messages
    from provider_accounts.model_discovery import (
        get_model_discovery as get_model_discovery,
    )
    from core.config import get_model_uri as get_model_uri
    from storage.db import get_store as get_store
    from storage.db import get_yandex_conversation as get_yandex_conversation
    from storage.generated_images import image_file_metadata as image_file_metadata
    from storage.db import is_provider_pending as is_provider_pending
    from conversations.ownership import (
        list_owned_conversations as list_owned_conversations,
    )
    from storage.db import (
        maybe_update_conversation_title as maybe_update_conversation_title,
    )
    import mcp_server.storage as mcp_storage
    from storage.db import normalize_conversation_title as normalize_conversation_title
    from yandex_client_modules.tool_config import (
        normalize_tools_config as normalize_tools_config,
    )
    from storage.generated_images import open_image_file as open_image_file
    from invocation.manager import persist_invocation_trace as persist_invocation_trace
    from yandex_client_modules.request_mixin import (
        provider_request_headers as provider_request_headers,
    )
    from provider_accounts.quotas import record_usage as record_usage
    from mcp_server.trace import (
        record_yandex_mcp_activity as record_yandex_mcp_activity,
    )
    from tool_providers.registry import registry as registry
    from provider_accounts.quotas import release_request as release_request
    from yandex_client_modules.image_generation import generate_image as request_image
    from provider_accounts.quotas import reserve_request as reserve_request
    from observability.trace_manager import reset_current_trace as reset_current_trace
    from storage.db import save_conv_settings as save_conv_settings
    from storage.generated_images import save_image_file as save_image_file
    from conversations.ownership import set_owner as set_owner
    from storage.db import set_yandex_conversation as set_yandex_conversation
    from economy.billing import settle_billing_to_treasury as settle_billing_to_treasury
    from invocation.manager import start_invocation as start_invocation
    from observability.trace_manager import traced_operation as traced_operation
    from provider_accounts.model_discovery import (
        validate_dialog_model as validate_dialog_model,
    )
    from yandex_client_modules.request_builder import (
        validate_generation_params as validate_generation_params,
    )

    return host.Host(
        CLASSIFIER_MODELS=CLASSIFIER_MODELS,
        Config=Config,
        EMBEDDING_MODELS=EMBEDDING_MODELS,
        ExecutionTrace=ExecutionTrace,
        IMAGE_MODELS=IMAGE_MODELS,
        IMAGE_SIZES=IMAGE_SIZES,
        ImageGenerationError=ImageGenerationError,
        KnowledgeCostConfig=KnowledgeCostConfig,
        MAX_PROMPT_CHARS=MAX_PROMPT_CHARS,
        MODEL_UPSTREAMS=MODEL_UPSTREAMS,
        ModelDiscoveryError=ModelDiscoveryError,
        OUTPUT_RATE=OUTPUT_RATE,
        ProviderQuotaExceeded=ProviderQuotaExceeded,
        REQUEST_TIMEOUT_SECONDS=REQUEST_TIMEOUT_SECONDS,
        RealtimeError=RealtimeError,
        RealtimeResult=RealtimeResult,
        SkillRegistry=SkillRegistry,
        SkillRegistryError=SkillRegistryError,
        TEXT_MODELS=TEXT_MODELS,
        TTS_MODEL=TTS_MODEL,
        UniversalToolCall=UniversalToolCall,
        UniversalToolExecutor=UniversalToolExecutor,
        VOICE_MODELS=VOICE_MODELS,
        YandexResponsesClient=YandexResponsesClient,
        _record_quota_usage=_record_quota_usage,
        _resolve_global_provider_credential=_resolve_global_provider_credential,
        _resolve_quota_user_id=_resolve_quota_user_id,
        _response_input=_response_input,
        _transport_timeout=_transport_timeout,
        add_message=add_message,
        aggregate_billing=aggregate_billing,
        bind_current_trace=bind_current_trace,
        build_ai_billing_item=build_ai_billing_item,
        build_generation_payload=build_generation_payload,
        calculate_knowledge_cost=calculate_knowledge_cost,
        check_access=check_access,
        completion_model_keys=completion_model_keys,
        compose_skill_instructions=compose_skill_instructions,
        create_conversation=create_conversation,
        create_invocation=create_invocation,
        create_invocation_trace=create_invocation_trace,
        fail_invocation=fail_invocation,
        finish_invocation=finish_invocation,
        generate_realtime=generate_realtime,
        get_conv_settings=get_conv_settings,
        get_conversation_title=get_conversation_title,
        get_current_owner_id=get_current_owner_id,
        get_current_trace=get_current_trace,
        get_message=get_message,
        get_messages=get_messages,
        get_model_discovery=get_model_discovery,
        get_model_uri=get_model_uri,
        get_store=get_store,
        get_yandex_conversation=get_yandex_conversation,
        image_file_metadata=image_file_metadata,
        is_provider_pending=is_provider_pending,
        list_owned_conversations=list_owned_conversations,
        maybe_update_conversation_title=maybe_update_conversation_title,
        mcp_storage=mcp_storage,
        normalize_conversation_title=normalize_conversation_title,
        normalize_tools_config=normalize_tools_config,
        open_image_file=open_image_file,
        persist_invocation_trace=persist_invocation_trace,
        provider_request_headers=provider_request_headers,
        record_usage=record_usage,
        record_yandex_mcp_activity=record_yandex_mcp_activity,
        registry=registry,
        release_request=release_request,
        request_image=request_image,
        reserve_request=reserve_request,
        reset_current_trace=reset_current_trace,
        save_conv_settings=save_conv_settings,
        save_image_file=save_image_file,
        set_owner=set_owner,
        set_yandex_conversation=set_yandex_conversation,
        settle_billing_to_treasury=settle_billing_to_treasury,
        start_invocation=start_invocation,
        traced_operation=traced_operation,
        validate_dialog_model=validate_dialog_model,
        validate_generation_params=validate_generation_params,
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
    from provider_accounts.model_discovery import (
        ModelDiscoveryError as ModelDiscoveryError,
    )
    from storage.db import delete_conversation as delete_conversation
    from storage.db import get_conv_settings as get_conv_settings
    from storage.db import get_conversations as get_conversations
    from economy.treasury_identity import get_current_owner_id as get_current_owner_id
    from agent_memory.client import get_memory_client as get_memory_client
    from storage.db import memory_file_path as memory_file_path
    from yandex_client_modules.tool_config import (
        normalize_tools_config as normalize_tools_config,
    )
    from storage.db import save_conv_settings as save_conv_settings
    from storage.db import update_conversation_model as update_conversation_model
    from storage.db import update_conversation_title as update_conversation_title
    from provider_accounts.model_discovery import (
        validate_dialog_model as validate_dialog_model,
    )
    from yandex_client_modules.request_builder import (
        validate_generation_params as validate_generation_params,
    )

    return host.Host(
        ConversationOwnershipStore=ConversationOwnershipStore,
        ModelDiscoveryError=ModelDiscoveryError,
        delete_conversation=delete_conversation,
        get_conv_settings=get_conv_settings,
        get_conversations=get_conversations,
        get_current_owner_id=get_current_owner_id,
        get_memory_client=get_memory_client,
        memory_file_path=memory_file_path,
        normalize_tools_config=normalize_tools_config,
        save_conv_settings=save_conv_settings,
        update_conversation_model=update_conversation_model,
        update_conversation_title=update_conversation_title,
        validate_dialog_model=validate_dialog_model,
        validate_generation_params=validate_generation_params,
    )


def _economy():
    from economy import host
    from core.config import ALL_MODELS as ALL_MODELS
    from core.config import AUDIO_STT_PRICE_PER_SEC as AUDIO_STT_PRICE_PER_SEC
    from core.config import AUDIO_TTS_PRICE_PER_SEC as AUDIO_TTS_PRICE_PER_SEC
    from cloud.base import CloudProviderError as CloudProviderError
    from kristall import KristallError as KristallError
    from core.config import canonical_model_key as canonical_model_key
    from observability.trace_manager import get_current_trace as get_current_trace
    from storage.db import get_store as get_store

    return host.Host(
        ALL_MODELS=ALL_MODELS,
        AUDIO_STT_PRICE_PER_SEC=AUDIO_STT_PRICE_PER_SEC,
        AUDIO_TTS_PRICE_PER_SEC=AUDIO_TTS_PRICE_PER_SEC,
        CloudProviderError=CloudProviderError,
        KristallError=KristallError,
        canonical_model_key=canonical_model_key,
        get_current_trace=get_current_trace,
        get_store=get_store,
    )


def _forge():
    from forge import host
    from economy.treasury_identity import get_current_owner_id as get_current_owner_id
    from storage.db import get_store as get_store

    return host.Host(get_current_owner_id=get_current_owner_id, get_store=get_store)


def _invocation():
    from invocation import host
    from observability.trace_manager import ExecutionTrace as ExecutionTrace
    from kristall import KristallError as KristallError
    from kristall import KristallLimitError as KristallLimitError
    from kristall.store import MAX_JOURNAL_FRAME_BYTES as MAX_JOURNAL_FRAME_BYTES
    from sessions.manager import create_session as create_session
    from agent_memory.client import get_memory_client as get_memory_client
    from storage.db import get_messages as get_messages
    from storage.db import memory_file_path as memory_file_path
    from sessions.manager import restore_session as restore_session

    return host.Host(
        ExecutionTrace=ExecutionTrace,
        KristallError=KristallError,
        KristallLimitError=KristallLimitError,
        MAX_JOURNAL_FRAME_BYTES=MAX_JOURNAL_FRAME_BYTES,
        create_session=create_session,
        get_memory_client=get_memory_client,
        get_messages=get_messages,
        memory_file_path=memory_file_path,
        restore_session=restore_session,
    )


def _logos_knowledge():
    from scripts import logos_knowledge_host as host
    from core.config import Config as Config
    from storage.memory_db import DatabaseError as DatabaseError
    from chat.mcp_routes import LogosClient as LogosClient
    from tool_providers.universal_tool_platform import (
        UniversalToolCall as UniversalToolCall,
    )
    from tool_providers.universal_tool_platform import (
        UniversalToolExecutor as UniversalToolExecutor,
    )
    from yandex_client_modules.errors import YandexClientError as YandexClientError
    from yandex_client_modules.request_mixin import (
        resolve_global_provider_credential as _resolve_global_provider_credential,
    )
    from invocation.manager import create_invocation as create_invocation
    from invocation.trace import create_invocation_trace as create_invocation_trace
    from invocation.manager import fail_invocation as fail_invocation
    import tool_providers.filesystem as filesystem
    from invocation.manager import finish_invocation as finish_invocation
    from composition.application import initialize as initialize_application
    from invocation.manager import persist_invocation_trace as persist_invocation_trace
    from tool_providers.registry import registry as registry
    from agent_runtime.issue_runner import (
        seed_provider_credential as seed_provider_credential,
    )
    from invocation.manager import start_invocation as start_invocation
    from tool_providers.universal_tool_platform import (
        validate_json_schema as validate_json_schema,
    )

    return host.Host(
        Config=Config,
        DatabaseError=DatabaseError,
        LogosClient=LogosClient,
        UniversalToolCall=UniversalToolCall,
        UniversalToolExecutor=UniversalToolExecutor,
        YandexClientError=YandexClientError,
        _resolve_global_provider_credential=_resolve_global_provider_credential,
        create_invocation=create_invocation,
        create_invocation_trace=create_invocation_trace,
        fail_invocation=fail_invocation,
        filesystem=filesystem,
        finish_invocation=finish_invocation,
        initialize_application=initialize_application,
        persist_invocation_trace=persist_invocation_trace,
        registry=registry,
        seed_provider_credential=seed_provider_credential,
        start_invocation=start_invocation,
        validate_json_schema=validate_json_schema,
    )


def _mcp_server():
    from mcp_server import host
    from core.config import ALL_MODELS as ALL_MODELS
    from agent_runtime.gateway import AgentGateway as AgentGateway
    from secret_store.credential_crypto import (
        CredentialCryptoError as CredentialCryptoError,
    )
    from runtime import RuntimeDispatcher as RuntimeDispatcher
    from runtime import RuntimeNotFound as RuntimeNotFound
    from runtime import RuntimeOperationNotFound as RuntimeOperationNotFound
    from runtime import RuntimeScopeViolation as RuntimeScopeViolation
    from tool_providers.universal_tool_platform import (
        UniversalToolCall as UniversalToolCall,
    )
    from tool_providers.universal_tool_platform import (
        UniversalToolExecutor as UniversalToolExecutor,
    )
    from observability.trace_manager import bind_current_trace as bind_current_trace
    from conversations.ownership import check_access as check_access
    from invocation.manager import create_invocation as create_invocation
    from invocation.trace import create_invocation_trace as create_invocation_trace
    from secret_store.credential_crypto import decrypt_secret as decrypt_secret
    from secret_store.credential_crypto import encrypt_secret as encrypt_secret
    from invocation.manager import fail_invocation as fail_invocation
    from invocation.manager import finish_invocation as finish_invocation
    from economy.treasury_identity import get_current_owner_id as get_current_owner_id
    from invocation.api import get_invocation_status as get_invocation_status
    from invocation.api import get_invocation_trace as get_invocation_trace
    from storage.db import get_messages as get_messages
    from conversations.ownership import get_owned_conversation as get_owned_conversation
    from sessions.manager import get_session as get_session
    from storage.db import get_store as get_store
    from tool_providers.filesystem import grep_search as grep_search
    from tool_providers.filesystem import list_directory as list_directory
    from conversations.ownership import (
        list_owned_conversations as list_owned_conversations,
    )
    from invocation.manager import persist_invocation_trace as persist_invocation_trace
    from tool_providers.filesystem import read_file as read_file
    from tool_providers.registry import registry as registry
    from observability.trace_manager import reset_current_trace as reset_current_trace
    from invocation.manager import start_invocation as start_invocation
    from tool_providers.universal_tool_platform import (
        tool_call_requires_approval as tool_call_requires_approval,
    )

    return host.Host(
        ALL_MODELS=ALL_MODELS,
        AgentGateway=AgentGateway,
        CredentialCryptoError=CredentialCryptoError,
        RuntimeDispatcher=RuntimeDispatcher,
        RuntimeNotFound=RuntimeNotFound,
        RuntimeOperationNotFound=RuntimeOperationNotFound,
        RuntimeScopeViolation=RuntimeScopeViolation,
        UniversalToolCall=UniversalToolCall,
        UniversalToolExecutor=UniversalToolExecutor,
        bind_current_trace=bind_current_trace,
        check_access=check_access,
        create_invocation=create_invocation,
        create_invocation_trace=create_invocation_trace,
        decrypt_secret=decrypt_secret,
        encrypt_secret=encrypt_secret,
        fail_invocation=fail_invocation,
        finish_invocation=finish_invocation,
        get_current_owner_id=get_current_owner_id,
        get_invocation_status=get_invocation_status,
        get_invocation_trace=get_invocation_trace,
        get_messages=get_messages,
        get_owned_conversation=get_owned_conversation,
        get_session=get_session,
        get_store=get_store,
        grep_search=grep_search,
        list_directory=list_directory,
        list_owned_conversations=list_owned_conversations,
        persist_invocation_trace=persist_invocation_trace,
        read_file=read_file,
        registry=registry,
        reset_current_trace=reset_current_trace,
        start_invocation=start_invocation,
        tool_call_requires_approval=tool_call_requires_approval,
    )


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


def _narration():
    from scripts import narration_host as host
    from api.yandex_transport import yandex_request as yandex_request

    return host.Host(yandex_request=yandex_request)


def _observability():
    from observability import host
    from economy.billing import aggregate_billing as aggregate_billing
    from economy.billing import build_ai_billing_item as build_ai_billing_item
    from economy.compute_resources import (
        build_compute_billing_item as build_compute_billing_item,
    )

    return host.Host(
        aggregate_billing=aggregate_billing,
        build_ai_billing_item=build_ai_billing_item,
        build_compute_billing_item=build_compute_billing_item,
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


def _printing3d():
    from printing3d import host
    from forge.binding import printing_store as printing_store

    return host.Host(printing_store=printing_store)


def _project_board():
    from project_board import host
    from memory_engine import MemoryStore as MemoryStore
    from memory_engine import StoreError as StoreError

    return host.Host(MemoryStore=MemoryStore, StoreError=StoreError)


def _provider_accounts():
    from provider_accounts import host
    from core.config import CLASSIFIER_MODELS as CLASSIFIER_MODELS
    from cloud.cloudru.api_key_provider import (
        CloudRuApiKeyProvider as CloudRuApiKeyProvider,
    )
    from cloud.cloudru.iam import CloudRuIamClient as CloudRuIamClient
    from cloud.cloudru.secret_management import (
        CloudRuSecretManagementClient as CloudRuSecretManagementClient,
    )
    from core.config import Config as Config
    from secret_store.credential_crypto import (
        CredentialCryptoError as CredentialCryptoError,
    )
    from core.config import EMBEDDING_MODELS as EMBEDDING_MODELS
    from core.config import IMAGE_MODELS as IMAGE_MODELS
    from core.config import TEXT_MODELS as TEXT_MODELS
    from core.config import VOICE_MODELS as VOICE_MODELS
    from yandex_client_modules.api_key_provider import (
        YandexApiKeyProvider as YandexApiKeyProvider,
    )
    from yandex_client_modules.request_mixin import (
        resolve_global_provider_credential as _resolve_global_provider_credential,
    )
    from core.model_profiles import annotate_catalog as annotate_catalog
    from core.config import canonical_model_key as canonical_model_key
    from core.config import config as config
    from secret_store.credential_crypto import decrypt_secret as decrypt_secret
    from secret_store.credential_crypto import encrypt_secret as encrypt_secret
    from secret_store.credential_crypto import (
        encryption_configured as encryption_configured,
    )
    from secret_store.credential_crypto import fingerprint_key as fingerprint_key
    from economy.treasury_identity import get_current_owner_id as get_current_owner_id
    from observability.trace_manager import get_current_trace as get_current_trace
    from core.config import get_model_uri as get_model_uri
    from storage.db import get_store as get_store
    from yandex_client_modules.request_mixin import (
        provider_request_headers as provider_request_headers,
    )
    from core.config import registry_key_for_upstream as registry_key_for_upstream
    from observability.trace_manager import traced_operation as traced_operation

    return host.Host(
        CLASSIFIER_MODELS=CLASSIFIER_MODELS,
        CloudRuApiKeyProvider=CloudRuApiKeyProvider,
        CloudRuIamClient=CloudRuIamClient,
        CloudRuSecretManagementClient=CloudRuSecretManagementClient,
        Config=Config,
        CredentialCryptoError=CredentialCryptoError,
        EMBEDDING_MODELS=EMBEDDING_MODELS,
        IMAGE_MODELS=IMAGE_MODELS,
        TEXT_MODELS=TEXT_MODELS,
        VOICE_MODELS=VOICE_MODELS,
        YandexApiKeyProvider=YandexApiKeyProvider,
        _resolve_global_provider_credential=_resolve_global_provider_credential,
        annotate_catalog=annotate_catalog,
        canonical_model_key=canonical_model_key,
        config=config,
        decrypt_secret=decrypt_secret,
        encrypt_secret=encrypt_secret,
        encryption_configured=encryption_configured,
        fingerprint_key=fingerprint_key,
        get_current_owner_id=get_current_owner_id,
        get_current_trace=get_current_trace,
        get_model_uri=get_model_uri,
        get_store=get_store,
        provider_request_headers=provider_request_headers,
        registry_key_for_upstream=registry_key_for_upstream,
        traced_operation=traced_operation,
    )


def _remote_runtime():
    from remote_runtime import host
    from observability.trace_manager import ExecutionTrace as ExecutionTrace
    from runtime import RuntimeDispatcher as RuntimeDispatcher
    from runtime import RuntimeLoader as RuntimeLoader
    from runtime import RuntimeNotFound as RuntimeNotFound
    from economy.treasury_identity import TreasuryIdentityError as TreasuryIdentityError
    from runtime.request_context import bind_runtime_request as bind_runtime_request
    from agent_memory.client import close_memory_clients as close_memory_clients
    from invocation.trace import create_invocation_trace as create_invocation_trace
    from storage.db import get_config as get_config
    from economy.treasury_identity import get_current_owner_id as get_current_owner_id
    from observability.trace_manager import get_current_trace as get_current_trace
    from storage.db import get_store as get_store
    from storage.db import init_db as init_runtime_db
    from storage.db import set_config as set_config

    return host.Host(
        ExecutionTrace=ExecutionTrace,
        RuntimeDispatcher=RuntimeDispatcher,
        RuntimeLoader=RuntimeLoader,
        RuntimeNotFound=RuntimeNotFound,
        TreasuryIdentityError=TreasuryIdentityError,
        bind_runtime_request=bind_runtime_request,
        close_memory_clients=close_memory_clients,
        create_invocation_trace=create_invocation_trace,
        get_config=get_config,
        get_current_owner_id=get_current_owner_id,
        get_current_trace=get_current_trace,
        get_store=get_store,
        init_runtime_db=init_runtime_db,
        set_config=set_config,
    )


def _runtime():
    from runtime import host
    from api.contracts import ContractViolation as ContractViolation
    from storage.contracts import StorageCredentialsMissing as StorageCredentialsMissing
    from storage.contracts import StorageError as StorageError
    from storage.contracts import (
        StorageProviderUnavailable as StorageProviderUnavailable,
    )
    from tool_providers.universal_tool_platform import (
        UniversalToolCall as UniversalToolCall,
    )
    from tool_providers.universal_tool_platform import (
        UniversalToolExecutor as UniversalToolExecutor,
    )
    from tool_providers.universal_tool_platform import (
        warn_deprecated_execution_hooks as _warn_deprecated_execution_hooks,
    )
    from conversations.ownership import check_access as check_access
    from sessions.profiles import clone_profile as clone_profile
    from invocation.manager import create_invocation as create_invocation
    from sessions.manager import create_session as create_session
    from economy.treasury_identity import get_current_owner_id as get_current_owner_id
    from invocation.api import get_invocation_status as get_invocation_status
    from invocation.api import get_invocation_trace as get_invocation_trace
    from sessions.profiles import get_profile as get_profile
    from sessions.manager import get_session as get_session
    from sessions.profiles import list_profiles as list_profiles
    from sessions.profiles import session_profile as session_profile
    from api.contracts import validate_api_contract as validate_api_contract

    return host.Host(
        ContractViolation=ContractViolation,
        StorageCredentialsMissing=StorageCredentialsMissing,
        StorageError=StorageError,
        StorageProviderUnavailable=StorageProviderUnavailable,
        UniversalToolCall=UniversalToolCall,
        UniversalToolExecutor=UniversalToolExecutor,
        _warn_deprecated_execution_hooks=_warn_deprecated_execution_hooks,
        check_access=check_access,
        clone_profile=clone_profile,
        create_invocation=create_invocation,
        create_session=create_session,
        get_current_owner_id=get_current_owner_id,
        get_invocation_status=get_invocation_status,
        get_invocation_trace=get_invocation_trace,
        get_profile=get_profile,
        get_session=get_session,
        list_profiles=list_profiles,
        session_profile=session_profile,
        validate_api_contract=validate_api_contract,
    )


def _secret_store():
    from secret_store import host
    from cloud.base import CloudProviderError as CloudProviderError
    from cloud.cloudru.secret_management import (
        CloudRuSecretManagementClient as CloudRuSecretManagementClient,
    )
    from observability.trace_manager import get_current_trace as get_current_trace
    from storage.db import get_store as get_store

    return host.Host(
        CloudProviderError=CloudProviderError,
        CloudRuSecretManagementClient=CloudRuSecretManagementClient,
        get_current_trace=get_current_trace,
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


def _tool_providers():
    from tool_providers import host
    from kristall import KristallError as KristallError
    from remote_runtime.ssh import SSHRuntimeError as SSHRuntimeError
    from remote_runtime.ssh_settings import (
        assert_operation_allowed as assert_operation_allowed,
    )
    from remote_runtime.ssh_settings import build_runtime as build_runtime
    from agent_runtime.local_gateway import (
        enqueue_local_tool_job as enqueue_local_tool_job,
    )
    from composition.tool_sources import SOURCES as external_tool_sources
    from storage.db import memory_file_path as memory_file_path
    from agent_runtime.local_gateway import (
        wait_for_local_tool_job as wait_for_local_tool_job,
    )

    return host.Host(
        KristallError=KristallError,
        SSHRuntimeError=SSHRuntimeError,
        assert_operation_allowed=assert_operation_allowed,
        build_runtime=build_runtime,
        enqueue_local_tool_job=enqueue_local_tool_job,
        external_tool_sources=external_tool_sources,
        memory_file_path=memory_file_path,
        wait_for_local_tool_job=wait_for_local_tool_job,
    )


def _web_shell():
    from web_shell import host
    from kristall import KristallError as KristallError
    from runtime import current_runtime_base_path as current_runtime_base_path
    from economy.treasury_identity import get_current_owner_id as get_current_owner_id
    from core.config import get_donation_url as get_donation_url
    from storage.db import get_store as get_store
    from storage.db import memory_file_path as memory_file_path
    from invocation.problems import problem as problem
    from composition.web import STORAGE_INITIALIZERS as storage_initializers

    return host.Host(
        KristallError=KristallError,
        current_runtime_base_path=current_runtime_base_path,
        get_current_owner_id=get_current_owner_id,
        get_donation_url=get_donation_url,
        get_store=get_store,
        memory_file_path=memory_file_path,
        problem=problem,
        storage_initializers=storage_initializers,
    )


def _workspace():
    from workspace import host
    from core.config import Config as Config
    from yandex_client_modules.errors import YandexClientError as YandexClientError
    from yandex_client_modules.client import (
        YandexResponsesClient as YandexResponsesClient,
    )

    return host.Host(
        Config=Config,
        YandexClientError=YandexClientError,
        YandexResponsesClient=YandexResponsesClient,
    )


def _yandex_client_modules():
    from yandex_client_modules import host
    from secret_store.credential_crypto import (
        CredentialCryptoError as CredentialCryptoError,
    )
    from observability.trace_manager import ExecutionTrace as ExecutionTrace
    from core.config import IMAGE_MODELS as IMAGE_MODELS
    from provider_accounts.quotas import ProviderQuotaExceeded as ProviderQuotaExceeded
    from tool_providers.universal_tool_platform import (
        UniversalToolCall as UniversalToolCall,
    )
    from tool_providers.universal_tool_platform import (
        UniversalToolExecutor as UniversalToolExecutor,
    )
    from secret_store.credential_crypto import decrypt_secret as decrypt_secret
    from secret_store.credential_crypto import encrypt_secret as encrypt_secret
    from agent_memory.manager import (
        get_controlled_memory_summary as get_controlled_memory_summary,
    )
    from storage.db import get_conv_settings as get_conv_settings
    from economy.treasury_identity import get_current_owner_id as get_current_owner_id
    from core.config import get_model_uri as get_model_uri
    from storage.db import get_store as get_store
    import mcp_server.storage as mcp_storage
    from provider_accounts.quotas import record_usage as record_usage
    from tool_providers.registry import registry as registry
    from provider_accounts.quotas import release_request as release_request
    from provider_accounts.quotas import reserve_request as reserve_request
    from provider_accounts.credentials import (
        resolve_client_credential as resolve_client_credential,
    )
    from core.model_profiles import ultra_profiles as ultra_profiles
    from core.model_profiles import (
        ultra_reasoning_controllable as ultra_reasoning_controllable,
    )
    from core.model_profiles import ultra_request_defaults as ultra_request_defaults
    from core.config import upstream_model_id as upstream_model_id

    return host.Host(
        CredentialCryptoError=CredentialCryptoError,
        ExecutionTrace=ExecutionTrace,
        IMAGE_MODELS=IMAGE_MODELS,
        ProviderQuotaExceeded=ProviderQuotaExceeded,
        UniversalToolCall=UniversalToolCall,
        UniversalToolExecutor=UniversalToolExecutor,
        decrypt_secret=decrypt_secret,
        encrypt_secret=encrypt_secret,
        get_controlled_memory_summary=get_controlled_memory_summary,
        get_conv_settings=get_conv_settings,
        get_current_owner_id=get_current_owner_id,
        get_model_uri=get_model_uri,
        get_store=get_store,
        mcp_storage=mcp_storage,
        record_usage=record_usage,
        registry=registry,
        release_request=release_request,
        reserve_request=reserve_request,
        resolve_client_credential=resolve_client_credential,
        ultra_profiles=ultra_profiles,
        ultra_reasoning_controllable=ultra_reasoning_controllable,
        ultra_request_defaults=ultra_request_defaults,
        upstream_model_id=upstream_model_id,
    )


# Each unit's host module and factory, in registration order.
_HOSTS = {
    "agent_memory": ("agent_memory.host", _agent_memory),
    "agent_runtime": ("agent_runtime.host", _agent_runtime),
    "agent_shell": ("agent_shell.host", _agent_shell),
    "chat": ("chat.host", _chat),
    "cloud": ("cloud.host", _cloud),
    "conversations": ("conversations.host", _conversations),
    "economy": ("economy.host", _economy),
    "forge": ("forge.host", _forge),
    "invocation": ("invocation.host", _invocation),
    "logos_knowledge": ("scripts.logos_knowledge_host", _logos_knowledge),
    "mcp_server": ("mcp_server.host", _mcp_server),
    "model_editor": ("model_editor.host", _model_editor),
    "narration": ("scripts.narration_host", _narration),
    "observability": ("observability.host", _observability),
    "organization": ("organization.host", _organization),
    "plugin_system": ("plugin_system.host", _plugin_system),
    "printing3d": ("printing3d.host", _printing3d),
    "project_board": ("project_board.host", _project_board),
    "provider_accounts": ("provider_accounts.host", _provider_accounts),
    "remote_runtime": ("remote_runtime.host", _remote_runtime),
    "runtime": ("runtime.host", _runtime),
    "secret_store": ("secret_store.host", _secret_store),
    "sessions": ("sessions.host", _sessions),
    "storage": ("storage.host", _storage),
    "tool_providers": ("tool_providers.host", _tool_providers),
    "web_shell": ("web_shell.host", _web_shell),
    "workspace": ("workspace.host", _workspace),
    "yandex_client_modules": ("yandex_client_modules.host", _yandex_client_modules),
}


def register_units(*units: str) -> None:
    """Register the factories of these units only; ``core/.env`` is not loaded."""
    for unit in units:
        module, factory = _HOSTS[unit]
        import_module(module).provide(factory)


def register_all() -> None:
    """Load ``core/.env`` and register every unit's factory."""
    load_env_file()
    register_units(*_HOSTS)
