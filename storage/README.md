# `storage`

Logos chat and configuration records on the native durable Kristall (`storage.db` is the facade the application uses), provider-neutral storage contracts, generated image artifacts, journal maintenance and the process-local engine used in development.

## Public API

`from storage import …`: `LocalDirectoryStorage`, `StorageObjectNotFound`, `StorageProvider`, `storage_provider_from_env`.

| Module | Purpose |
| --- | --- |
| [`storage.contracts`](contracts.py) | Provider-neutral storage contracts and the repository-local adapter. |
| [`storage.db`](db.py) | Logos chat/config repositories backed by the native durable Kristall. |
| [`storage.generated_images`](generated_images.py) | Private generated image artifacts, kept outside Kristall's bounded text values. |
| [`storage.memory_db`](memory_db.py) | Small process-local database engine used for deterministic tests and local development. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `storage.StorageObjectNotFound` | `model_editor` |
| `storage.contracts.StorageCredentialsMissing` | `cloud`, `runtime` |
| `storage.contracts.StorageError` | `cloud`, `runtime` |
| `storage.contracts.StorageObject` | `cloud` |
| `storage.contracts.StorageObjectNotFound` | `cloud` |
| `storage.contracts.StorageProviderUnavailable` | `cloud`, `runtime` |
| `storage.contracts.safe_relative_path` | `cloud` |
| `storage.contracts.storage_provider_from_env` | `model_editor` |
| `storage.db.add_message` | `chat` |
| `storage.db.create_conversation` | `chat` |
| `storage.db.delete_conversation` | `conversations` |
| `storage.db.get_config` | `remote_runtime` |
| `storage.db.get_conv_settings` | `chat`, `conversations`, `yandex_client_modules` |
| `storage.db.get_conversation_title` | `chat` |
| `storage.db.get_conversations` | `conversations` |
| `storage.db.get_message` | `chat` |
| `storage.db.get_messages` | `chat`, `invocation`, `mcp_server` |
| `storage.db.get_store` | `agent_runtime`, `chat`, `economy`, `forge`, `mcp_server`, `organization`, `provider_accounts`, `remote_runtime`, `secret_store`, `web_shell`, `yandex_client_modules` |
| `storage.db.get_yandex_conversation` | `chat` |
| `storage.db.init_db` | `remote_runtime` |
| `storage.db.is_provider_pending` | `chat` |
| `storage.db.maybe_update_conversation_title` | `chat` |
| `storage.db.memory_file_path` | `agent_memory`, `conversations`, `invocation`, `sessions`, `tool_providers`, `web_shell` |
| `storage.db.normalize_conversation_title` | `chat` |
| `storage.db.save_conv_settings` | `chat`, `conversations` |
| `storage.db.set_config` | `remote_runtime` |
| `storage.db.set_yandex_conversation` | `chat` |
| `storage.db.update_conversation_model` | `conversations` |
| `storage.db.update_conversation_title` | `conversations` |
| `storage.generated_images.image_file_metadata` | `chat` |
| `storage.generated_images.open_image_file` | `chat` |
| `storage.generated_images.save_image_file` | `chat` |
| `storage.memory_db.DatabaseError` | `agent_runtime`, `tools.knowledge` |

## Ports

Everything `storage` uses from other units is a field of `Host` in [`storage/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `CloudRuObjectStorage` | `cloud.cloudru.object_storage.CloudRuObjectStorage` |
| `KristallError` | `kristall.KristallError` |
| `current_runtime_data_root` | `runtime.request_context.current_runtime_data_root` |
| `get_memory_client` | `agent_memory.client.get_memory_client` |

## Install

- `composition/wiring.py` registers the `storage` host factory (`register_all()`, or `register_units("storage")` for this unit alone); the host is built on first use.
- Storage start-up (`web_shell`) runs `storage.db.init_db`.
