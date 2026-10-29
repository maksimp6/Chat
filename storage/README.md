# `storage`

Logos chat and configuration records in the native durable Kristall journal (`storage.records`; `storage.db` is its former name and still works), provider-neutral storage contracts, generated image artifacts, journal maintenance and the process-local engine used in development.

## Role in a dialog

Where this unit sits in the [life of a dialog](../docs/architecture/modules.md#life-of-a-dialog):

- **Open Logos**: `GET /`, `GET /api/version`
- **List dialogs**: `GET /api/conversations`
- **Choose a model**: `GET /api/models`
- **Create a dialog**: `POST /api/conversations`
- **Open a dialog and read its history**: `GET /api/conversations/<conv_id>/messages`
- **Rename a dialog or change its model**: `PATCH /api/conversations/<conv_id>`
- **Send a message (the turn)**: `POST /api/chat`
- **Tools during the turn**: `POST /api/chat`, `GET /api/mcp-servers`
- **Approve a tool action**: `POST /api/mcp/execute-approved`
- **Photos, pictures and voice**: `POST /api/images/generate`, `GET /api/images/requests/<request_id>`, `POST /api/voice/session`, `GET /api/voice/events`, `POST /api/voice/text`, `POST /api/voice/audio`
- **Trace, cost and treasury of a reply**: `GET /api/invocations/<invocation_id>/trace`, `GET /api/treasury/account`
- **Global memory used by every dialog**: `GET /api/memory/manage`
- **Delete a dialog**: `DELETE /api/conversations/<conv_id>`
- **Read dialogs from outside (MCP)**: `POST {MCP_PATH}`

## Public API

`from storage import …`: `LocalDirectoryStorage`, `StorageObjectNotFound`, `StorageProvider`, `storage_provider_from_env`.

| Module | Purpose |
| --- | --- |
| [`storage.contracts`](contracts.py) | Provider-neutral storage contracts and the repository-local adapter. |
| [`storage.generated_images`](generated_images.py) | Private generated image artifacts, kept outside Kristall's bounded text values. |
| [`storage.memory_db`](memory_db.py) | Small process-local database engine used for deterministic tests and local development. |
| [`storage.records`](records.py) | Logos chat and configuration records in the native durable Kristall journal. |

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
| `storage.generated_images.image_file_metadata` | `chat` |
| `storage.generated_images.open_image_file` | `chat` |
| `storage.generated_images.save_image_file` | `chat` |
| `storage.memory_db.DatabaseError` | `agent_runtime`, `tools.knowledge` |
| `storage.records.add_message` | `chat` |
| `storage.records.create_conversation` | `chat` |
| `storage.records.delete_conversation` | `conversations` |
| `storage.records.get_config` | `remote_runtime` |
| `storage.records.get_conv_settings` | `chat`, `conversations`, `yandex_client_modules` |
| `storage.records.get_conversation_title` | `chat` |
| `storage.records.get_conversations` | `conversations` |
| `storage.records.get_message` | `chat` |
| `storage.records.get_messages` | `chat`, `invocation`, `mcp_server` |
| `storage.records.get_store` | `agent_runtime`, `chat`, `economy`, `forge`, `mcp_server`, `organization`, `provider_accounts`, `remote_runtime`, `secret_store`, `web_shell`, `yandex_client_modules` |
| `storage.records.get_yandex_conversation` | `chat` |
| `storage.records.is_provider_pending` | `chat` |
| `storage.records.maybe_update_conversation_title` | `chat` |
| `storage.records.memory_file_path` | `agent_memory`, `conversations`, `invocation`, `sessions`, `tool_providers`, `web_shell` |
| `storage.records.normalize_conversation_title` | `chat` |
| `storage.records.open_journal_store` | `remote_runtime` |
| `storage.records.save_conv_settings` | `chat`, `conversations` |
| `storage.records.set_config` | `remote_runtime` |
| `storage.records.set_yandex_conversation` | `chat` |
| `storage.records.update_conversation_model` | `conversations` |
| `storage.records.update_conversation_title` | `conversations` |

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
- Storage start-up (`web_shell`) runs `storage.records.open_journal_store`.
