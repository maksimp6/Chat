# `provider_accounts`

Model-provider accounts: deployment credentials and their API, provider-agnostic key rotation and the rotation workers' logic, import of runner-issued keys, per-user quotas and their API, and model discovery with the model catalog API.

## Role in a dialog

Where this unit sits in the [life of a dialog](../docs/architecture/modules.md#life-of-a-dialog):

- **Choose a model**: `GET /api/models`
- **Create a dialog**: `POST /api/conversations`
- **Rename a dialog or change its model**: `PATCH /api/conversations/<conv_id>`
- **Send a message (the turn)**: `POST /api/chat`
- **Photos, pictures and voice**: `POST /api/images/generate`, `GET /api/images/requests/<request_id>`, `POST /api/voice/session`, `GET /api/voice/events`, `POST /api/voice/text`, `POST /api/voice/audio`

## Public API

`from provider_accounts import …`: `credentials`, `credentials_routes`, `key_rotation_cli`, `model_discovery`, `model_routes`, `quota_routes`, `quotas`.

| Module | Purpose |
| --- | --- |
| [`provider_accounts.credentials`](credentials.py) | Provider-aware deployment credential storage and resolution. |
| [`provider_accounts.credentials_routes`](credentials_routes.py) | Provider credential configuration and health-check API. |
| [`provider_accounts.key_rotation_cli`](key_rotation_cli.py) | Deployment-wide provider key rotation. |
| [`provider_accounts.model_discovery`](model_discovery.py) | Provider-backed model discovery with a bounded in-process cache. |
| [`provider_accounts.model_routes`](model_routes.py) | HTTP API of the model catalog. |
| [`provider_accounts.quota_routes`](quota_routes.py) | HTTP API for inspecting and configuring per-user provider quotas. |
| [`provider_accounts.quotas`](quotas.py) | Per-user provider usage quotas and rate limiting. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `provider_accounts.credentials.bootstrap_credential` | `agent_runtime` |
| `provider_accounts.credentials.resolve_client_credential` | `yandex_client_modules` |
| `provider_accounts.model_discovery.ModelDiscoveryError` | `chat`, `conversations` |
| `provider_accounts.model_discovery.get_model_discovery` | `chat` |
| `provider_accounts.model_discovery.validate_dialog_model` | `chat`, `conversations` |
| `provider_accounts.quotas.ProviderQuotaExceeded` | `chat`, `yandex_client_modules` |
| `provider_accounts.quotas.record_usage` | `chat`, `yandex_client_modules` |
| `provider_accounts.quotas.release_request` | `chat`, `yandex_client_modules` |
| `provider_accounts.quotas.reserve_request` | `chat`, `yandex_client_modules` |

## Ports

Everything `provider_accounts` uses from other units is a field of `Host` in [`provider_accounts/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

<details>
<summary>22 ports</summary>

| Port | Provided by |
| --- | --- |
| `CLASSIFIER_MODELS` | `core.config.CLASSIFIER_MODELS` |
| `CloudRuApiKeyProvider` | `cloud.cloudru.api_key_provider.CloudRuApiKeyProvider` |
| `CloudRuIamClient` | `cloud.cloudru.iam.CloudRuIamClient` |
| `Config` | `core.config.Config` |
| `CredentialCryptoError` | `secret_store.credential_crypto.CredentialCryptoError` |
| `EMBEDDING_MODELS` | `core.config.EMBEDDING_MODELS` |
| `IMAGE_MODELS` | `core.config.IMAGE_MODELS` |
| `TEXT_MODELS` | `core.config.TEXT_MODELS` |
| `VOICE_MODELS` | `core.config.VOICE_MODELS` |
| `annotate_catalog` | `core.model_profiles.annotate_catalog` |
| `canonical_model_key` | `core.config.canonical_model_key` |
| `config` | `core.config.config` |
| `decrypt_secret` | `secret_store.credential_crypto.decrypt_secret` |
| `encrypt_secret` | `secret_store.credential_crypto.encrypt_secret` |
| `encryption_configured` | `secret_store.credential_crypto.encryption_configured` |
| `fingerprint_key` | `secret_store.credential_crypto.fingerprint_key` |
| `get_current_owner_id` | `economy.treasury_identity.get_current_owner_id` |
| `get_current_trace` | `observability.trace_manager.get_current_trace` |
| `get_model_uri` | `core.config.get_model_uri` |
| `get_store` | `storage.records.get_store` |
| `registry_key_for_upstream` | `core.config.registry_key_for_upstream` |
| `traced_operation` | `observability.trace_manager.traced_operation` |

</details>

## Install

- `composition/wiring.py` registers the `provider_accounts` host factory (`register_all()`, or `register_units("provider_accounts")` for this unit alone); the host is built on first use.
- `composition/web.py` registers the blueprints `provider_credentials_bp` (`provider_accounts.credentials_routes`), `provider_quota_bp` (`provider_accounts.quota_routes`).
- `composition/web.py` calls `provider_accounts.model_routes.install(app)`.
- Storage start-up (`web_shell`) runs `provider_accounts.quotas.prepare_quota_records`.
