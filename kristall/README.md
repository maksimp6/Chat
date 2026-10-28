# `kristall`

Kristall: a synchronous typed key/value store with client-owned explicit name/value commits on a native RAM/journal engine, with optional authenticated, ordered replication.

## Public API

`from kristall import …`: `Commit`, `DatabaseInfo`, `DatabaseInfoContract`, `Kristall`, `Store`, `KristallError`, `KristallLimitError`, `Transaction`.

| Module | Purpose |
| --- | --- |
| [`kristall.store`](store.py) | Minimal synchronous typed key/value Kristall. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `kristall.Kristall` | `agent_memory` |
| `kristall.KristallError` | `agent_shell`, `economy`, `invocation`, `storage`, `tool_providers`, `web_shell` |
| `kristall.KristallLimitError` | `invocation` |
| `kristall.store.MAX_JOURNAL_FRAME_BYTES` | `invocation` |

## Ports

None. The unit imports only the standard library, third-party packages and its own modules.

## Install

- Nothing to wire: import the modules you need.
