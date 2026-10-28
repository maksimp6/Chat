# `yandex_client_modules`

The Yandex AI Studio client: the Responses API client with its request, polling, parsing, MCP, conversation and file mixins, the Realtime WebSocket and image-generation transports, hosted-tool settings, the API-key provider and a minimal SDK.

## Role in a dialog

Where this unit sits in the [life of a dialog](../docs/architecture/modules.md#life-of-a-dialog):

- **Choose a model**: `GET /api/models`
- **Dialog settings and tools**: `GET /api/conversations/<conv_id>/settings`, `PUT /api/conversations/<conv_id>/settings`, `GET /api/tools/categories`, `PUT /api/conversations/<conv_id>/tools`
- **Send a message (the turn)**: `POST /api/chat`
- **Tools during the turn**: `POST /api/chat`, `GET /api/mcp-servers`, `POST /api/mcp-servers`
- **Photos, pictures and voice**: `POST /api/images/generate`, `GET /api/images/requests/<request_id>`, `POST /api/voice/session`, `GET /api/voice/events`, `POST /api/voice/text`, `POST /api/voice/audio`

## Public API

`from yandex_client_modules import …`: `api_key_provider`, `client`, `errors`, `image_generation`, `realtime`, `request_builder`, `request_mixin`, `sdk`, `tool_config`.

| Module | Purpose |
| --- | --- |
| [`yandex_client_modules.api_key_provider`](api_key_provider.py) | Concrete Yandex Cloud API-key provider used by the rotation worker. |
| [`yandex_client_modules.client`](client.py) | The Yandex AI Studio Responses API client, assembled from the request, polling, conversation, MCP and file mixins. |
| [`yandex_client_modules.errors`](errors.py) | Errors raised by the Yandex client. |
| [`yandex_client_modules.image_generation`](image_generation.py) | Alice AI ART through the synchronous OpenAI-compatible Yandex Images API. |
| [`yandex_client_modules.realtime`](realtime.py) | Yandex Realtime WebSocket transport, with server-side credentials only. |
| [`yandex_client_modules.request_builder`](request_builder.py) | Request payload helpers for the Yandex Responses API. |
| [`yandex_client_modules.request_mixin`](request_mixin.py) | Request mixin of the Responses client: credentials, per-user quotas, transport timeouts, request capture and ask(). |
| [`yandex_client_modules.sdk`](sdk.py) | sdk.py - Минимальный SDK для работы с Yandex AI Studio |
| [`yandex_client_modules.tool_config`](tool_config.py) | Validate and normalize the hosted-tool settings shared by API and client. |

Command line:

- `python -m yandex_client_modules.sdk`

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `yandex_client_modules.api_key_provider.YandexApiKeyProvider` | `provider_accounts` |
| `yandex_client_modules.client.YandexResponsesClient` | `chat`, `workspace` |
| `yandex_client_modules.errors.YandexClientError` | `tools.knowledge`, `workspace` |
| `yandex_client_modules.image_generation.IMAGE_SIZES` | `chat` |
| `yandex_client_modules.image_generation.ImageGenerationError` | `chat` |
| `yandex_client_modules.image_generation.MAX_PROMPT_CHARS` | `chat` |
| `yandex_client_modules.image_generation.REQUEST_TIMEOUT_SECONDS` | `chat` |
| `yandex_client_modules.image_generation.build_generation_payload` | `chat` |
| `yandex_client_modules.image_generation.generate_image` | `chat` |
| `yandex_client_modules.realtime.MODEL_UPSTREAMS` | `chat` |
| `yandex_client_modules.realtime.OUTPUT_RATE` | `chat` |
| `yandex_client_modules.realtime.RealtimeError` | `chat` |
| `yandex_client_modules.realtime.RealtimeResult` | `chat` |
| `yandex_client_modules.realtime.TTS_MODEL` | `chat` |
| `yandex_client_modules.realtime.generate_realtime` | `chat` |
| `yandex_client_modules.request_builder.response_input` | `chat` |
| `yandex_client_modules.request_builder.validate_generation_params` | `chat`, `conversations` |
| `yandex_client_modules.request_mixin.provider_request_headers` | `chat`, `provider_accounts` |
| `yandex_client_modules.request_mixin.record_quota_usage` | `chat` |
| `yandex_client_modules.request_mixin.resolve_global_provider_credential` | `chat`, `provider_accounts`, `tools.knowledge` |
| `yandex_client_modules.request_mixin.resolve_quota_user_id` | `chat` |
| `yandex_client_modules.request_mixin.transport_timeout` | `chat` |
| `yandex_client_modules.tool_config.normalize_tools_config` | `chat`, `conversations` |

## Ports

Everything `yandex_client_modules` uses from other units is a field of `Host` in [`yandex_client_modules/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

<details>
<summary>19 ports</summary>

| Port | Provided by |
| --- | --- |
| `CredentialCryptoError` | `secret_store.credential_crypto.CredentialCryptoError` |
| `ExecutionTrace` | `observability.trace_manager.ExecutionTrace` |
| `IMAGE_MODELS` | `core.config.IMAGE_MODELS` |
| `ProviderQuotaExceeded` | `provider_accounts.quotas.ProviderQuotaExceeded` |
| `UniversalToolCall` | `tool_providers.universal_tool_platform.UniversalToolCall` |
| `decrypt_secret` | `secret_store.credential_crypto.decrypt_secret` |
| `encrypt_secret` | `secret_store.credential_crypto.encrypt_secret` |
| `get_conv_settings` | `storage.records.get_conv_settings` |
| `get_current_owner_id` | `economy.treasury_identity.get_current_owner_id` |
| `get_model_uri` | `core.config.get_model_uri` |
| `get_store` | `storage.records.get_store` |
| `mcp_storage` | `mcp_server.storage` |
| `record_usage` | `provider_accounts.quotas.record_usage` |
| `registry` | `tool_providers.registry.registry` |
| `release_request` | `provider_accounts.quotas.release_request` |
| `reserve_request` | `provider_accounts.quotas.reserve_request` |
| `ultra_profiles` | `core.model_profiles.ultra_profiles` |
| `ultra_request_defaults` | `core.model_profiles.ultra_request_defaults` |
| `upstream_model_id` | `core.config.upstream_model_id` |

</details>

## Install

- `composition/wiring.py` registers the `yandex_client_modules` host factory (`register_all()`, or `register_units("yandex_client_modules")` for this unit alone); the host is built on first use.
