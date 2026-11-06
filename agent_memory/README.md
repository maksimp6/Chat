# Nemura

`agent_memory` · Stores shared, source-linked memory facts for Logos agents.

Shared memory for Logos agents on native Kristall: the Kristall client lifecycle, fact extraction, the memory panel's controls and HTTP API, and durable conversation-ownership and user-identity records.

## Role in a dialog

Where this unit sits in the [life of a dialog](../docs/architecture/modules.md#life-of-a-dialog):

- **Tools during the turn**: `POST /api/chat`, `GET /api/mcp-servers`
- **Global memory used by every dialog**: `GET /api/memory/manage`

## Public API

`from agent_memory import …`: `client`, `conversation_ownership_store`, `extractor`, `manager`, `routes`.

| Module | Purpose |
| --- | --- |
| [`agent_memory.client`](client.py) | Logos-owned lifecycle for the standalone explicit-commit Kristall. |
| [`agent_memory.conversation_ownership_store`](conversation_ownership_store.py) | Durable conversation ownership records on the native Kristall engine. |
| [`agent_memory.extractor`](extractor.py) | Extract facts and persist individual records directly in native Kristall; each accepted fact also goes into `vector_db` (collection `memory_facts`). |
| [`agent_memory.manager`](manager.py) | Native Kristall configuration and fact controls. |
| [`agent_memory.routes`](routes.py) | HTTP API of the global memory panel: configuration and facts, read-only. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `agent_memory.client.close_memory_clients` | `remote_runtime` |
| `agent_memory.client.get_memory_client` | `agent_shell`, `conversations`, `invocation`, `sessions`, `storage` |
| `agent_memory.conversation_ownership_store.ConversationOwnershipStore` | `conversations` |
| `agent_memory.manager.get_controlled_memory_summary` | `yandex_client_modules` |

## Ports

Everything `agent_memory` uses from other units is a field of `Host` in [`agent_memory/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `kristall_Kristall` | `kristall.Kristall` |
| `memory_file_path` | `storage.records.memory_file_path` |
| `vector_add` | `vector_db.store.add` |

## Install

- `composition/wiring.py` registers the `agent_memory` host factory (`register_all()`, or `register_units("agent_memory")` for this unit alone); the host is built on first use.
- `composition/web.py` calls `agent_memory.routes.install(app)`.
