# Dynamic model discovery

Alice Pro fetches the current text-model catalog from the configured Yandex AI Studio OpenAI-compatible `/models` endpoint.

## Runtime behavior

- The provider catalog is cached in-process for `MODEL_DISCOVERY_TTL_SECONDS` (default: 300 seconds).
- `GET /api/models?refresh=1` forces an immediate refresh.
- Provider model IDs are normalized to the IDs used by Alice Pro model URIs.
- Known pricing metadata is preserved when a discovered model matches the static catalog.
- Voice models remain available from the existing voice catalog because the provider model catalog is text-model oriented.
- If discovery fails or the provider credential is missing, `/api/models` returns the static catalog with `degraded: true` and the stable error code `model_discovery_unavailable`.
- Provider credentials are never included in the response or diagnostic message.

## Configuration

- `YANDEX_BASE_URL`: provider OpenAI-compatible base URL.
- `YANDEX_API_KEY` or legacy `YC_API_KEY`: API key used for model discovery.
- `MODEL_DISCOVERY_TTL_SECONDS`: cache lifetime.
- `MODEL_DISCOVERY_TIMEOUT_SECONDS`: provider request timeout.

The API key is sent using `Authorization: Api-Key <API_KEY>`, matching the Yandex Cloud API-key authentication contract.

## Refresh and degraded mode

The model selector continues to work when the provider catalog is temporarily unavailable by using the static catalog. A caller that needs current data can request `?refresh=1`.

Future credential/configuration update flows should call the refresh endpoint after successfully changing provider credentials so the next model selection uses the new provider catalog.