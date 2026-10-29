"""Ports of the chat unit: everything it uses from other units.

A composition root provides them (``composition/wiring.py``); code in this unit
reads them only when it runs. Call ports keep their original names; other ports
are reached as ``bound.<name>``.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any

from chat._binding import Binding, HostNotBound


@dataclass(frozen=True, kw_only=True)
class Host:
    CLASSIFIER_MODELS: Any  # core.config.CLASSIFIER_MODELS
    Config: Any  # core.config.Config
    EMBEDDING_MODELS: Any  # core.config.EMBEDDING_MODELS
    EMBEDDING_MODES: Any  # yandex_client_modules.embeddings.MODES
    EmbeddingError: Any  # yandex_client_modules.embeddings.EmbeddingError
    ExecutionTrace: Any  # observability.trace_manager.ExecutionTrace
    ExpiredCredentialError: Any  # provider_accounts.credentials.ExpiredCredentialError
    IMAGE_MODELS: Any  # core.config.IMAGE_MODELS
    IMAGE_SIZES: Any  # yandex_client_modules.image_generation.IMAGE_SIZES
    ImageGenerationError: (
        Any  # yandex_client_modules.image_generation.ImageGenerationError
    )
    MAX_PROMPT_CHARS: Any  # yandex_client_modules.image_generation.MAX_PROMPT_CHARS
    MODEL_UPSTREAMS: Any  # yandex_client_modules.realtime.MODEL_UPSTREAMS
    ModelDiscoveryError: Any  # provider_accounts.model_discovery.ModelDiscoveryError
    NoActiveCredentialError: (
        Any  # provider_accounts.credentials.NoActiveCredentialError
    )
    OUTPUT_RATE: Any  # yandex_client_modules.realtime.OUTPUT_RATE
    ProviderQuotaExceeded: Any  # provider_accounts.quotas.ProviderQuotaExceeded
    REQUEST_TIMEOUT_SECONDS: (
        Any  # yandex_client_modules.image_generation.REQUEST_TIMEOUT_SECONDS
    )
    RealtimeError: Any  # yandex_client_modules.realtime.RealtimeError
    RealtimeResult: Any  # yandex_client_modules.realtime.RealtimeResult
    SkillRegistry: Any  # agent_skills.registry.SkillRegistry
    SkillRegistryError: Any  # agent_skills.registry.SkillRegistryError
    TEXT_MODELS: Any  # core.config.TEXT_MODELS
    TTS_MODEL: Any  # yandex_client_modules.realtime.TTS_MODEL
    UniversalToolCall: Any  # tool_providers.universal_tool_platform.UniversalToolCall
    UniversalToolExecutor: (
        Any  # tool_providers.universal_tool_platform.UniversalToolExecutor
    )
    V2_EMBEDDING_DIMENSIONS: Any  # yandex_client_modules.embeddings.V2_DIMENSIONS
    VOICE_MODELS: Any  # core.config.VOICE_MODELS
    YandexResponsesClient: Any  # yandex_client_modules.client.YandexResponsesClient
    _record_quota_usage: Any  # yandex_client_modules.request_mixin.record_quota_usage
    _resolve_global_provider_credential: (
        Any  # yandex_client_modules.request_mixin.resolve_global_provider_credential
    )
    _resolve_quota_user_id: (
        Any  # yandex_client_modules.request_mixin.resolve_quota_user_id
    )
    _response_input: Any  # yandex_client_modules.request_builder.response_input
    _transport_timeout: Any  # yandex_client_modules.request_mixin.transport_timeout
    add_message: Any  # storage.records.add_message
    aggregate_billing: Any  # economy.billing.aggregate_billing
    bind_current_trace: Any  # observability.trace_manager.bind_current_trace
    build_ai_billing_item: Any  # economy.billing.build_ai_billing_item
    build_generation_payload: (
        Any  # yandex_client_modules.image_generation.build_generation_payload
    )
    check_access: Any  # conversations.ownership.check_access
    completion_model_keys: Any  # core.model_profiles.completion_model_keys
    compose_skill_instructions: Any  # agent_skills.registry.compose_skill_instructions
    create_conversation: Any  # storage.records.create_conversation
    create_invocation: Any  # invocation.manager.create_invocation
    create_invocation_trace: Any  # invocation.trace.create_invocation_trace
    embed_texts: Any  # yandex_client_modules.embeddings.embed_texts
    embedding_cost: Any  # economy.knowledge_economics.embedding_cost
    fail_invocation: Any  # invocation.manager.fail_invocation
    file_court_case: Any  # court.filing.file_case
    finish_invocation: Any  # invocation.manager.finish_invocation
    generate_realtime: Any  # yandex_client_modules.realtime.generate_realtime
    get_conversation_title: Any  # storage.records.get_conversation_title
    get_current_owner_id: Any  # economy.treasury_identity.get_current_owner_id
    get_current_trace: Any  # observability.trace_manager.get_current_trace
    get_message: Any  # storage.records.get_message
    get_messages: Any  # storage.records.get_messages
    get_model_discovery: Any  # provider_accounts.model_discovery.get_model_discovery
    get_model_uri: Any  # core.config.get_model_uri
    get_store: Any  # storage.records.get_store
    get_yandex_conversation: Any  # storage.records.get_yandex_conversation
    image_file_metadata: Any  # storage.generated_images.image_file_metadata
    is_provider_pending: Any  # storage.records.is_provider_pending
    list_owned_conversations: Any  # conversations.ownership.list_owned_conversations
    load_memory_config: Any  # agent_memory.manager.load_memory_config
    maybe_update_conversation_title: (
        Any  # storage.records.maybe_update_conversation_title
    )
    mcp_storage: Any  # mcp_server.storage
    normalize_conversation_title: Any  # storage.records.normalize_conversation_title
    open_image_file: Any  # storage.generated_images.open_image_file
    persist_invocation_trace: Any  # invocation.manager.persist_invocation_trace
    provider_request_headers: (
        Any  # yandex_client_modules.request_mixin.provider_request_headers
    )
    record_usage: Any  # provider_accounts.quotas.record_usage
    record_yandex_mcp_activity: Any  # mcp_server.trace.record_yandex_mcp_activity
    registry: Any  # tool_providers.registry.registry
    release_request: Any  # provider_accounts.quotas.release_request
    request_image: Any  # yandex_client_modules.image_generation.generate_image
    reserve_request: Any  # provider_accounts.quotas.reserve_request
    reset_current_trace: Any  # observability.trace_manager.reset_current_trace
    save_global_memory_facts: Any  # agent_memory.extractor.save_global_memory_facts
    save_image_file: Any  # storage.generated_images.save_image_file
    set_owner: Any  # conversations.ownership.set_owner
    set_yandex_conversation: Any  # storage.records.set_yandex_conversation
    settle_billing_to_treasury: Any  # economy.billing.settle_billing_to_treasury
    start_invocation: Any  # invocation.manager.start_invocation
    traced_operation: Any  # observability.trace_manager.traced_operation
    validate_dialog_model: (
        Any  # provider_accounts.model_discovery.validate_dialog_model
    )
    validate_generation_params: (
        Any  # yandex_client_modules.request_builder.validate_generation_params
    )


_binding: Binding[Host] = Binding("chat")
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
    "add_message",
    "aggregate_billing",
    "bind_current_trace",
    "build_ai_billing_item",
    "build_generation_payload",
    "check_access",
    "completion_model_keys",
    "compose_skill_instructions",
    "create_conversation",
    "create_invocation",
    "create_invocation_trace",
    "embed_texts",
    "embedding_cost",
    "fail_invocation",
    "file_court_case",
    "finish_invocation",
    "generate_realtime",
    "get_conversation_title",
    "get_current_owner_id",
    "get_current_trace",
    "get_message",
    "get_messages",
    "get_model_discovery",
    "get_model_uri",
    "get_store",
    "get_yandex_conversation",
    "image_file_metadata",
    "is_provider_pending",
    "list_owned_conversations",
    "load_memory_config",
    "maybe_update_conversation_title",
    "normalize_conversation_title",
    "open_image_file",
    "persist_invocation_trace",
    "provider_request_headers",
    "record_usage",
    "record_yandex_mcp_activity",
    "release_request",
    "request_image",
    "reserve_request",
    "reset_current_trace",
    "save_global_memory_facts",
    "save_image_file",
    "set_owner",
    "set_yandex_conversation",
    "settle_billing_to_treasury",
    "start_invocation",
    "traced_operation",
    "validate_dialog_model",
    "validate_generation_params",
]


def add_message(*args: Any, **kwargs: Any) -> Any:
    return current().add_message(*args, **kwargs)


def aggregate_billing(*args: Any, **kwargs: Any) -> Any:
    return current().aggregate_billing(*args, **kwargs)


def bind_current_trace(*args: Any, **kwargs: Any) -> Any:
    return current().bind_current_trace(*args, **kwargs)


def build_ai_billing_item(*args: Any, **kwargs: Any) -> Any:
    return current().build_ai_billing_item(*args, **kwargs)


def build_generation_payload(*args: Any, **kwargs: Any) -> Any:
    return current().build_generation_payload(*args, **kwargs)


def check_access(*args: Any, **kwargs: Any) -> Any:
    return current().check_access(*args, **kwargs)


def completion_model_keys(*args: Any, **kwargs: Any) -> Any:
    return current().completion_model_keys(*args, **kwargs)


def compose_skill_instructions(*args: Any, **kwargs: Any) -> Any:
    return current().compose_skill_instructions(*args, **kwargs)


def create_conversation(*args: Any, **kwargs: Any) -> Any:
    return current().create_conversation(*args, **kwargs)


def create_invocation(*args: Any, **kwargs: Any) -> Any:
    return current().create_invocation(*args, **kwargs)


def create_invocation_trace(*args: Any, **kwargs: Any) -> Any:
    return current().create_invocation_trace(*args, **kwargs)


def embed_texts(*args: Any, **kwargs: Any) -> Any:
    return current().embed_texts(*args, **kwargs)


def embedding_cost(*args: Any, **kwargs: Any) -> Any:
    return current().embedding_cost(*args, **kwargs)


def fail_invocation(*args: Any, **kwargs: Any) -> Any:
    return current().fail_invocation(*args, **kwargs)


def file_court_case(*args: Any, **kwargs: Any) -> Any:
    return current().file_court_case(*args, **kwargs)


def finish_invocation(*args: Any, **kwargs: Any) -> Any:
    return current().finish_invocation(*args, **kwargs)


def generate_realtime(*args: Any, **kwargs: Any) -> Any:
    return current().generate_realtime(*args, **kwargs)


def get_conversation_title(*args: Any, **kwargs: Any) -> Any:
    return current().get_conversation_title(*args, **kwargs)


def get_current_owner_id(*args: Any, **kwargs: Any) -> Any:
    return current().get_current_owner_id(*args, **kwargs)


def get_current_trace(*args: Any, **kwargs: Any) -> Any:
    return current().get_current_trace(*args, **kwargs)


def get_message(*args: Any, **kwargs: Any) -> Any:
    return current().get_message(*args, **kwargs)


def get_messages(*args: Any, **kwargs: Any) -> Any:
    return current().get_messages(*args, **kwargs)


def get_model_discovery(*args: Any, **kwargs: Any) -> Any:
    return current().get_model_discovery(*args, **kwargs)


def get_model_uri(*args: Any, **kwargs: Any) -> Any:
    return current().get_model_uri(*args, **kwargs)


def get_store(*args: Any, **kwargs: Any) -> Any:
    return current().get_store(*args, **kwargs)


def get_yandex_conversation(*args: Any, **kwargs: Any) -> Any:
    return current().get_yandex_conversation(*args, **kwargs)


def image_file_metadata(*args: Any, **kwargs: Any) -> Any:
    return current().image_file_metadata(*args, **kwargs)


def is_provider_pending(*args: Any, **kwargs: Any) -> Any:
    return current().is_provider_pending(*args, **kwargs)


def list_owned_conversations(*args: Any, **kwargs: Any) -> Any:
    return current().list_owned_conversations(*args, **kwargs)


def load_memory_config(*args: Any, **kwargs: Any) -> Any:
    return current().load_memory_config(*args, **kwargs)


def maybe_update_conversation_title(*args: Any, **kwargs: Any) -> Any:
    return current().maybe_update_conversation_title(*args, **kwargs)


def normalize_conversation_title(*args: Any, **kwargs: Any) -> Any:
    return current().normalize_conversation_title(*args, **kwargs)


def open_image_file(*args: Any, **kwargs: Any) -> Any:
    return current().open_image_file(*args, **kwargs)


def persist_invocation_trace(*args: Any, **kwargs: Any) -> Any:
    return current().persist_invocation_trace(*args, **kwargs)


def provider_request_headers(*args: Any, **kwargs: Any) -> Any:
    return current().provider_request_headers(*args, **kwargs)


def record_usage(*args: Any, **kwargs: Any) -> Any:
    return current().record_usage(*args, **kwargs)


def record_yandex_mcp_activity(*args: Any, **kwargs: Any) -> Any:
    return current().record_yandex_mcp_activity(*args, **kwargs)


def release_request(*args: Any, **kwargs: Any) -> Any:
    return current().release_request(*args, **kwargs)


def request_image(*args: Any, **kwargs: Any) -> Any:
    return current().request_image(*args, **kwargs)


def reserve_request(*args: Any, **kwargs: Any) -> Any:
    return current().reserve_request(*args, **kwargs)


def reset_current_trace(*args: Any, **kwargs: Any) -> Any:
    return current().reset_current_trace(*args, **kwargs)


def save_global_memory_facts(*args: Any, **kwargs: Any) -> Any:
    return current().save_global_memory_facts(*args, **kwargs)


def save_image_file(*args: Any, **kwargs: Any) -> Any:
    return current().save_image_file(*args, **kwargs)


def set_owner(*args: Any, **kwargs: Any) -> Any:
    return current().set_owner(*args, **kwargs)


def set_yandex_conversation(*args: Any, **kwargs: Any) -> Any:
    return current().set_yandex_conversation(*args, **kwargs)


def settle_billing_to_treasury(*args: Any, **kwargs: Any) -> Any:
    return current().settle_billing_to_treasury(*args, **kwargs)


def start_invocation(*args: Any, **kwargs: Any) -> Any:
    return current().start_invocation(*args, **kwargs)


def validate_dialog_model(*args: Any, **kwargs: Any) -> Any:
    return current().validate_dialog_model(*args, **kwargs)


def validate_generation_params(*args: Any, **kwargs: Any) -> Any:
    return current().validate_generation_params(*args, **kwargs)


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
