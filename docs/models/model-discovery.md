# Dynamic model discovery

Logos reads the available models from the configured Yandex AI Studio OpenAI-compatible `/models` endpoint. The response keeps the existing text, voice, image, and embedding types in separate sections; an embedding or image URI is never offered as a text model. Classifiers are not supported and their entries are skipped, including `cls://` URIs and explicit classifier metadata.

## Runtime behavior

- The provider catalog is cached in-process for `MODEL_DISCOVERY_TTL_SECONDS` (default: 300 seconds), scoped to the credential source, key fingerprint, and folder. The fingerprint is internal and is not returned in the model catalog.
- `GET /api/models?refresh=1` forces an immediate refresh.
- Every catalog read resolves the current deployment-wide provider credential through the same resolver as chat, before considering the cache. Saving or rotating a credential cannot reuse a catalog belonging to another key or folder.
- Recognized model IDs and equivalent legacy URIs map to their registry keys without changing the model version. Unknown explicit versions and tuned model URIs retain their complete identifier, including version and tuning suffix; a different folder's URI is not imported.
- `provider_model_id` records the exact identifier returned by the provider. It is not reconstructed by discarding path segments or appending `/latest`.
- Known pricing metadata is preserved when a discovered model matches the static catalog.
- Unknown tariffs remain unknown. A discovered non-text deployment without a registered adapter is listed with `available: false` and `availability_reason: runtime_adapter_not_registered`.
- Registered voice, image, and embedding entries also remain in their corresponding sections when not returned by Models API. Their presence is a local capability declaration, not proof that the active key can invoke them.
- If discovery fails or the provider credential is missing, `/api/models` returns the static catalog with `degraded: true` and the stable error code `model_discovery_unavailable`.
- Provider credentials are never included in the response or diagnostic message.

## Configuration

- `YANDEX_BASE_URL`: provider OpenAI-compatible base URL.
- Discovery uses the active encrypted provider credential, with the same credential-resolution behavior as chat. It does not capture `YANDEX_API_KEY` or `YC_API_KEY` once at application startup.
- `MODEL_DISCOVERY_TTL_SECONDS`: cache lifetime.
- `MODEL_DISCOVERY_TIMEOUT_SECONDS`: provider request timeout.

The request sends `Authorization: Api-Key <API_KEY>` and the active folder in `OpenAI-Project` and `x-project`. The latter is the header shown in the official [Models API cURL example](https://aistudio.yandex.ru/ru/docs/ai-studio/operations/models/get). The key needs the `yc.ai.models.viewer` scope; list access does not itself establish generation permission.

## Refresh and degraded mode

The model selector continues to work when the provider catalog is temporarily unavailable by using the static catalog. A caller that needs current data can request `?refresh=1`.

Credential update flows invalidate the cache. Credential identity is also checked on every catalog read, so invalidation is not the only protection against stale credentials. A successful list request is not a generation, tool execution, or Android acceptance check.
