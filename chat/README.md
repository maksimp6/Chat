# Kelura

`chat` · Serves chat turns, voice, images, embeddings and completions.

The chat: conversation routes that record the full execution chain, and the voice (Yandex Realtime), image generation (Alice AI ART), embeddings, prompt classification and synchronous completion endpoints.

## Role in a dialog

Where this unit sits in the [life of a dialog](../docs/architecture/modules.md#life-of-a-dialog):

- **List dialogs**: `GET /api/conversations`
- **Create a dialog**: `POST /api/conversations`
- **Open a dialog and read its history**: `GET /api/conversations/<conv_id>/messages`
- **Settings and tools (global, read-only)**: `GET /api/settings`, `GET /api/tools/categories`
- **Send a message (the turn)**: `POST /api/chat`
- **Approve a tool action**: `POST /api/mcp/execute-approved`
- **Photos, pictures and voice**: `POST /api/images/generate`, `GET /api/images/requests/<request_id>`, `POST /api/voice/session`, `GET /api/voice/events`, `POST /api/voice/text`, `POST /api/voice/audio`

## Public API

`from chat import …`: `classification_routes`, `completion_routes`, `embedding_routes`, `image_routes`, `mcp_routes`, `voice_routes`.

| Module | Purpose |
| --- | --- |
| [`chat.classification_routes`](classification_routes.py) | Prompt classifiers have their own transport; they are not chat models. |
| [`chat.completion_routes`](completion_routes.py) | POST /api/completion: native synchronous YandexGPT Pro 5.1 completions. |
| [`chat.embedding_routes`](embedding_routes.py) | POST /api/embeddings: the four public Yandex embedding models. |
| [`chat.image_routes`](image_routes.py) | Generate one Alice AI ART picture per durable, conversation-bound request. |
| [`chat.live_turns`](live_turns.py) | Live chat turns: the event log a browser reads as server-sent events while a turn runs, and the durable receipt that makes a turn re-sent with the same `request_id` run only once. |
| [`chat.mcp_routes`](mcp_routes.py) | Маршруты чата с сохранением полной цепочки выполнения в БД. |
| [`chat.voice_routes`](voice_routes.py) | HTTP/SSE facade for the selected Yandex Realtime WebSocket model. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `chat.mcp_routes.ForkError` | `court` |
| `chat.mcp_routes.LogosClient` | `agent_runtime`, `tools.knowledge` |
| `chat.mcp_routes.call_yandex_operation` | `court` |
| `chat.mcp_routes.fork_dialog` | `court` |

## Ports

Everything `chat` uses from other units is a field of `Host` in [`chat/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

<details>
<summary>94 ports</summary>

| Port | Provided by |
| --- | --- |
| `CHAT_MODEL` | `core.config.CHAT_MODEL` |
| `CLASSIFIER_MODELS` | `core.config.CLASSIFIER_MODELS` |
| `Config` | `core.config.Config` |
| `EMBEDDING_MODELS` | `core.config.EMBEDDING_MODELS` |
| `EMBEDDING_MODES` | `yandex_client_modules.embeddings.MODES` |
| `EmbeddingError` | `yandex_client_modules.embeddings.EmbeddingError` |
| `ExecutionTrace` | `observability.trace_manager.ExecutionTrace` |
| `ExpiredCredentialError` | `provider_accounts.credentials.ExpiredCredentialError` |
| `IMAGE_MODELS` | `core.config.IMAGE_MODELS` |
| `IMAGE_SIZES` | `yandex_client_modules.image_generation.IMAGE_SIZES` |
| `ImageGenerationError` | `yandex_client_modules.image_generation.ImageGenerationError` |
| `MAX_PROMPT_CHARS` | `yandex_client_modules.image_generation.MAX_PROMPT_CHARS` |
| `MODEL_UPSTREAMS` | `yandex_client_modules.realtime.MODEL_UPSTREAMS` |
| `NoActiveCredentialError` | `provider_accounts.credentials.NoActiveCredentialError` |
| `OLLAMA_MODELS` | `ollama_client.settings.MODELS` |
| `OllamaClient` | `ollama_client.client.OllamaClient` |
| `OUTPUT_RATE` | `yandex_client_modules.realtime.OUTPUT_RATE` |
| `ProviderQuotaExceeded` | `provider_accounts.quotas.ProviderQuotaExceeded` |
| `REQUEST_TIMEOUT_SECONDS` | `yandex_client_modules.image_generation.REQUEST_TIMEOUT_SECONDS` |
| `RealtimeError` | `yandex_client_modules.realtime.RealtimeError` |
| `RealtimeResult` | `yandex_client_modules.realtime.RealtimeResult` |
| `SkillRegistry` | `agent_skills.registry.SkillRegistry` |
| `SkillRegistryError` | `agent_skills.registry.SkillRegistryError` |
| `TEXT_MODELS` | `core.config.TEXT_MODELS` |
| `TTS_MODEL` | `yandex_client_modules.realtime.TTS_MODEL` |
| `UniversalToolCall` | `tool_providers.universal_tool_platform.UniversalToolCall` |
| `UniversalToolExecutor` | `tool_providers.universal_tool_platform.UniversalToolExecutor` |
| `V2_EMBEDDING_DIMENSIONS` | `yandex_client_modules.embeddings.V2_DIMENSIONS` |
| `VOICE_MODELS` | `core.config.VOICE_MODELS` |
| `YandexResponsesClient` | `yandex_client_modules.client.YandexResponsesClient` |
| `_record_quota_usage` | `yandex_client_modules.request_mixin.record_quota_usage` |
| `_resolve_global_provider_credential` | `yandex_client_modules.request_mixin.resolve_global_provider_credential` |
| `_resolve_quota_user_id` | `yandex_client_modules.request_mixin.resolve_quota_user_id` |
| `_response_input` | `yandex_client_modules.request_builder.response_input` |
| `_transport_timeout` | `yandex_client_modules.request_mixin.transport_timeout` |
| `add_message` | `storage.records.add_message` |
| `aggregate_billing` | `economy.billing.aggregate_billing` |
| `bind_current_trace` | `observability.trace_manager.bind_current_trace` |
| `build_ai_billing_item` | `economy.billing.build_ai_billing_item` |
| `build_generation_payload` | `yandex_client_modules.image_generation.build_generation_payload` |
| `censor_reply` | `court.hearings.censor_reply` |
| `check_access` | `conversations.ownership.check_access` |
| `compose_skill_instructions` | `agent_skills.registry.compose_skill_instructions` |
| `create_conversation` | `storage.records.create_conversation` |
| `create_invocation` | `invocation.manager.create_invocation` |
| `conversation_turn_running` | `invocation.manager.conversation_turn_running` |
| `create_invocation_trace` | `invocation.trace.create_invocation_trace` |
| `embed_texts` | `yandex_client_modules.embeddings.embed_texts` |
| `embedding_cost` | `economy.knowledge_economics.embedding_cost` |
| `fail_invocation` | `invocation.manager.fail_invocation` |
| `file_court_case` | `court.filing.file_case` |
| `finish_invocation` | `invocation.manager.finish_invocation` |
| `generate_realtime` | `yandex_client_modules.realtime.generate_realtime` |
| `get_conversation_title` | `storage.records.get_conversation_title` |
| `get_current_owner_id` | `economy.treasury_identity.get_current_owner_id` |
| `get_current_trace` | `observability.trace_manager.get_current_trace` |
| `get_message` | `storage.records.get_message` |
| `get_messages` | `storage.records.get_messages` |
| `get_model_discovery` | `provider_accounts.model_discovery.get_model_discovery` |
| `get_model_uri` | `core.config.get_model_uri` |
| `get_store` | `storage.records.get_store` |
| `get_yandex_conversation` | `storage.records.get_yandex_conversation` |
| `image_file_metadata` | `storage.generated_images.image_file_metadata` |
| `is_provider_pending` | `storage.records.is_provider_pending` |
| `list_owned_conversations` | `conversations.ownership.list_owned_conversations` |
| `load_memory_config` | `agent_memory.manager.load_memory_config` |
| `maybe_update_conversation_title` | `storage.records.maybe_update_conversation_title` |
| `mcp_storage` | `mcp_server.storage` |
| `mcp_oauth` | `mcp_server.oauth` |
| `normalize_conversation_title` | `storage.records.normalize_conversation_title` |
| `open_image_file` | `storage.generated_images.open_image_file` |
| `persist_invocation_trace` | `invocation.manager.persist_invocation_trace` |
| `provider_request_headers` | `yandex_client_modules.request_mixin.provider_request_headers` |
| `read_upload` | `workspace.upload_routes.read_upload` |
| `record_court_reply` | `court.hearings.record_reply` |
| `record_usage` | `provider_accounts.quotas.record_usage` |
| `record_yandex_mcp_activity` | `mcp_server.trace.record_yandex_mcp_activity` |
| `registry` | `tool_providers.registry.registry` |
| `release_request` | `provider_accounts.quotas.release_request` |
| `request_image` | `yandex_client_modules.image_generation.generate_image` |
| `reserve_request` | `provider_accounts.quotas.reserve_request` |
| `reset_current_trace` | `observability.trace_manager.reset_current_trace` |
| `save_global_memory_facts` | `agent_memory.extractor.save_global_memory_facts` |
| `save_image_file` | `storage.generated_images.save_image_file` |
| `set_owner` | `conversations.ownership.set_owner` |
| `set_yandex_conversation` | `storage.records.set_yandex_conversation` |
| `settle_billing_to_treasury` | `economy.billing.settle_billing_to_treasury` |
| `start_invocation` | `invocation.manager.start_invocation` |
| `traced_operation` | `observability.trace_manager.traced_operation` |
| `validate_api_contract` | `api.contracts.validate_api_contract` |
| `validate_dialog_model` | `provider_accounts.model_discovery.validate_dialog_model` |
| `vector_add` | `vector_db.store.add` |
| `vector_sense` | `vector_db.sense.answer` |
| `validate_generation_params` | `yandex_client_modules.request_builder.validate_generation_params` |

</details>

## Install

- `composition/wiring.py` registers the `chat` host factory (`register_all()`, or `register_units("chat")` for this unit alone); the host is built on first use.
- `composition/web.py` registers the blueprints `mcp_bp` (`chat.mcp_routes`), `voice_bp` (`chat.voice_routes`), `image_bp` (`chat.image_routes`), `embedding_bp` (`chat.embedding_routes`), `classification_bp` (`chat.classification_routes`), `completion_bp` (`chat.completion_routes`).
