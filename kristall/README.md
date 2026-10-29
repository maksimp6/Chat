# `kristall`

Kristall: a synchronous typed key/value store with client-owned explicit name/value commits on a native RAM/journal engine, with optional authenticated, ordered replication.

## Role in a dialog

Where this unit sits in the [life of a dialog](../docs/architecture/modules.md#life-of-a-dialog):

- **Open Logos**: `GET /`, `GET /api/version`
- **List dialogs**: `GET /api/conversations`
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

`from kristall import …`: `Commit`, `DatabaseInfo`, `DatabaseInfoContract`, `Kristall`, `Store`, `KristallError`, `KristallLimitError`, `Transaction`.

| Module | Purpose |
| --- | --- |
| [`kristall.store`](store.py) | Minimal synchronous typed key/value Kristall. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `kristall.Kristall` | `agent_memory` |
| `kristall.KristallError` | `agent_runtime`, `agent_shell`, `economy`, `invocation`, `storage`, `tool_providers`, `tools.knowledge`, `web_shell` |
| `kristall.KristallLimitError` | `invocation` |
| `kristall.store.MAX_JOURNAL_FRAME_BYTES` | `invocation` |

## Ports

None. The unit imports only the standard library, third-party packages and its own modules.

## Install

- Nothing to wire: import the modules you need.
