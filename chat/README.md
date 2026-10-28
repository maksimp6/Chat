# `chat`

The chat: conversation routes that record the full execution chain, and the voice (Yandex Realtime), image generation (Alice AI ART), embeddings, prompt classification and synchronous completion endpoints.

## Public API

`from chat import …`: `classification_routes`, `completion_routes`, `embedding_routes`, `image_routes`, `mcp_routes`, `voice_routes`.

| Module | Purpose |
| --- | --- |
| [`chat.classification_routes`](classification_routes.py) | Prompt classifiers have their own transport; they are not chat models. |
| [`chat.completion_routes`](completion_routes.py) | POST /api/completion: native synchronous YandexGPT Pro 5.1 completions. |
| [`chat.embedding_routes`](embedding_routes.py) | POST /api/embeddings: the four public Yandex embedding models. |
| [`chat.image_routes`](image_routes.py) | Generate one Alice AI ART picture per durable, conversation-bound request. |
| [`chat.mcp_routes`](mcp_routes.py) | Маршруты чата с сохранением полной цепочки выполнения в БД. |
| [`chat.voice_routes`](voice_routes.py) | HTTP/SSE facade for the selected Yandex Realtime WebSocket model. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `chat.mcp_routes.LogosClient` | `agent_runtime`, `tools.knowledge` |

## Ports

Everything `chat` uses from other units is a field of `Host` in [`chat/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

<details>
<summary>69 ports</summary>

| Port | Provided by |
| --- | --- |
| `CLASSIFIER_MODELS` | `core.config.CLASSIFIER_MODELS` |
| `Config` | `core.config.Config` |
| `EMBEDDING_MODELS` | `core.config.EMBEDDING_MODELS` |
| `ExecutionTrace` | `observability.trace_manager.ExecutionTrace` |
| `IMAGE_MODELS` | `core.config.IMAGE_MODELS` |
| `IMAGE_SIZES` | `yandex_client_modules.image_generation.IMAGE_SIZES` |
| `KnowledgeCostConfig` | `economy.knowledge_economics.KnowledgeCostConfig` |
| `MAX_PROMPT_CHARS` | `yandex_client_modules.image_generation.MAX_PROMPT_CHARS` |
| `MODEL_UPSTREAMS` | `yandex_client_modules.realtime.MODEL_UPSTREAMS` |
| `ModelDiscoveryError` | `provider_accounts.model_discovery.ModelDiscoveryError` |
| `OUTPUT_RATE` | `yandex_client_modules.realtime.OUTPUT_RATE` |
| `ProviderQuotaExceeded` | `provider_accounts.quotas.ProviderQuotaExceeded` |
| `RealtimeError` | `yandex_client_modules.realtime.RealtimeError` |
| `RealtimeResult` | `yandex_client_modules.realtime.RealtimeResult` |
| `SkillRegistry` | `agent_skills.registry.SkillRegistry` |
| `SkillRegistryError` | `agent_skills.registry.SkillRegistryError` |
| `TEXT_MODELS` | `core.config.TEXT_MODELS` |
| `TTS_MODEL` | `yandex_client_modules.realtime.TTS_MODEL` |
| `UniversalToolCall` | `tool_providers.universal_tool_platform.UniversalToolCall` |
| `VOICE_MODELS` | `core.config.VOICE_MODELS` |
| `YandexResponsesClient` | `yandex_client_modules.client.YandexResponsesClient` |
| `_record_quota_usage` | `yandex_client_modules.request_mixin.record_quota_usage` |
| `_response_input` | `yandex_client_modules.request_builder.response_input` |
| `_transport_timeout` | `yandex_client_modules.request_mixin.transport_timeout` |
| `add_message` | `storage.db.add_message` |
| `aggregate_billing` | `economy.billing.aggregate_billing` |
| `bind_current_trace` | `observability.trace_manager.bind_current_trace` |
| `build_ai_billing_item` | `economy.billing.build_ai_billing_item` |
| `check_access` | `conversations.ownership.check_access` |
| `completion_model_keys` | `core.model_profiles.completion_model_keys` |
| `compose_skill_instructions` | `agent_skills.registry.compose_skill_instructions` |
| `create_conversation` | `storage.db.create_conversation` |
| `create_invocation` | `invocation.manager.create_invocation` |
| `create_invocation_trace` | `invocation.trace.create_invocation_trace` |
| `fail_invocation` | `invocation.manager.fail_invocation` |
| `finish_invocation` | `invocation.manager.finish_invocation` |
| `generate_realtime` | `yandex_client_modules.realtime.generate_realtime` |
| `get_conv_settings` | `storage.db.get_conv_settings` |
| `get_conversation_title` | `storage.db.get_conversation_title` |
| `get_current_owner_id` | `economy.treasury_identity.get_current_owner_id` |
| `get_current_trace` | `observability.trace_manager.get_current_trace` |
| `get_message` | `storage.db.get_message` |
| `get_messages` | `storage.db.get_messages` |
| `get_model_discovery` | `provider_accounts.model_discovery.get_model_discovery` |
| `get_model_uri` | `core.config.get_model_uri` |
| `get_store` | `storage.db.get_store` |
| `get_yandex_conversation` | `storage.db.get_yandex_conversation` |
| `image_file_metadata` | `storage.generated_images.image_file_metadata` |
| `is_provider_pending` | `storage.db.is_provider_pending` |
| `list_owned_conversations` | `conversations.ownership.list_owned_conversations` |
| `maybe_update_conversation_title` | `storage.db.maybe_update_conversation_title` |
| `mcp_storage` | `mcp_server.storage` |
| `normalize_conversation_title` | `storage.db.normalize_conversation_title` |
| `open_image_file` | `storage.generated_images.open_image_file` |
| `persist_invocation_trace` | `invocation.manager.persist_invocation_trace` |
| `record_usage` | `provider_accounts.quotas.record_usage` |
| `record_yandex_mcp_activity` | `mcp_server.trace.record_yandex_mcp_activity` |
| `registry` | `tool_providers.registry.registry` |
| `release_request` | `provider_accounts.quotas.release_request` |
| `request_image` | `yandex_client_modules.image_generation.generate_image` |
| `reserve_request` | `provider_accounts.quotas.reserve_request` |
| `reset_current_trace` | `observability.trace_manager.reset_current_trace` |
| `save_conv_settings` | `storage.db.save_conv_settings` |
| `save_image_file` | `storage.generated_images.save_image_file` |
| `set_owner` | `conversations.ownership.set_owner` |
| `set_yandex_conversation` | `storage.db.set_yandex_conversation` |
| `settle_billing_to_treasury` | `economy.billing.settle_billing_to_treasury` |
| `start_invocation` | `invocation.manager.start_invocation` |
| `traced_operation` | `observability.trace_manager.traced_operation` |

</details>

## Install

- `composition/wiring.py` registers the `chat` host factory (`register_all()`, or `register_units("chat")` for this unit alone); the host is built on first use.
- `composition/web.py` registers the blueprints `mcp_bp` (`chat.mcp_routes`), `voice_bp` (`chat.voice_routes`), `image_bp` (`chat.image_routes`), `embedding_bp` (`chat.embedding_routes`), `classification_bp` (`chat.classification_routes`), `completion_bp` (`chat.completion_routes`).
