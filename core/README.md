# `core`

Process-wide configuration: the model catalog and pricing, the runtime view of the prepared model profiles, the process environment and small shared helpers.

## Public API

`from core import …`: `config`, `env`, `model_profiles`.

| Module | Purpose |
| --- | --- |
| [`core.config`](config.py) | Logos - Configuration & Pricing |
| [`core.env`](env.py) | The one place that reads or writes the process environment. |
| [`core.model_profiles`](model_profiles.py) | Runtime view of the prepared Ultra model profiles. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `core.config.ALL_MODELS` | `economy`, `mcp_server` |
| `core.config.AUDIO_STT_PRICE_PER_SEC` | `economy` |
| `core.config.AUDIO_TTS_PRICE_PER_SEC` | `economy` |
| `core.config.CLASSIFIER_MODELS` | `chat`, `provider_accounts` |
| `core.config.Config` | `agent_runtime`, `chat`, `provider_accounts`, `tools.knowledge`, `workspace` |
| `core.config.EMBEDDING_MODELS` | `chat`, `provider_accounts` |
| `core.config.IMAGE_MODELS` | `chat`, `provider_accounts`, `yandex_client_modules` |
| `core.config.TEXT_MODELS` | `chat`, `provider_accounts` |
| `core.config.VOICE_MODELS` | `chat`, `provider_accounts` |
| `core.config.canonical_model_key` | `economy`, `provider_accounts` |
| `core.config.config` | `provider_accounts` |
| `core.config.get_donation_url` | `web_shell` |
| `core.config.get_model_uri` | `chat`, `provider_accounts`, `yandex_client_modules` |
| `core.config.registry_key_for_upstream` | `provider_accounts` |
| `core.config.upstream_model_id` | `yandex_client_modules` |
| `core.model_profiles.annotate_catalog` | `provider_accounts` |
| `core.model_profiles.completion_model_keys` | `chat` |
| `core.model_profiles.ultra_profiles` | `yandex_client_modules` |
| `core.model_profiles.ultra_reasoning_controllable` | `yandex_client_modules` |
| `core.model_profiles.ultra_request_defaults` | `yandex_client_modules` |

## Ports

None. The unit imports only the standard library, third-party packages and its own modules.

## Install

- Nothing to wire: import the modules you need.
