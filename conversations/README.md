# `conversations`

Conversations: ownership records of shared local conversations, helpers for creating Yandex conversations, and the HTTP API of one conversation (title, model, deletion and dialog settings).

## Role in a dialog

Where this unit sits in the [life of a dialog](../docs/architecture/modules.md#life-of-a-dialog):

- **List dialogs**: `GET /api/conversations`
- **Create a dialog**: `POST /api/conversations`
- **Open a dialog and read its history**: `GET /api/conversations/<conv_id>/messages`
- **Settings and tools (global, read-only)**: `GET /api/settings`, `GET /api/tools/categories`
- **Rename a dialog or change its model**: `PATCH /api/conversations/<conv_id>`
- **Send a message (the turn)**: `POST /api/chat`
- **Delete a dialog**: `DELETE /api/conversations/<conv_id>`
- **Read dialogs from outside (MCP)**: `POST {MCP_PATH}`

## Public API

`from conversations import …`: `ownership`, `routes`.

| Module | Purpose |
| --- | --- |
| [`conversations.ownership`](ownership.py) | Legacy ownership metadata for shared local conversations. |
| [`conversations.routes`](routes.py) | HTTP API of one conversation: title, model, deletion and dialog settings. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `conversations.ownership.check_access` | `chat`, `mcp_server`, `runtime` |
| `conversations.ownership.get_owned_conversation` | `mcp_server` |
| `conversations.ownership.list_owned_conversations` | `chat`, `mcp_server` |
| `conversations.ownership.set_owner` | `chat` |

## Ports

Everything `conversations` uses from other units is a field of `Host` in [`conversations/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `ModelDiscoveryError` | `provider_accounts.model_discovery.ModelDiscoveryError` |
| `delete_conversation` | `storage.records.delete_conversation` |
| `get_conv_settings` | `storage.records.get_conv_settings` |
| `get_conversations` | `storage.records.get_conversations` |
| `get_current_owner_id` | `economy.treasury_identity.get_current_owner_id` |
| `get_memory_client` | `agent_memory.client.get_memory_client` |
| `memory_file_path` | `storage.records.memory_file_path` |
| `save_conv_settings` | `storage.records.save_conv_settings` |
| `update_conversation_model` | `storage.records.update_conversation_model` |
| `update_conversation_title` | `storage.records.update_conversation_title` |

## Install

- `composition/wiring.py` registers the `conversations` host factory (`register_all()`, or `register_units("conversations")` for this unit alone); the host is built on first use.
- `composition/web.py` calls `conversations.routes.install(app)`.
- Storage start-up (`web_shell`) runs `conversations.ownership.prepare_conversation_ownership_records`.
