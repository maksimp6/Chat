# `memory_engine`

Memory DB: a synchronous typed key/value store with client-owned explicit commits, and a dedicated Unix-socket service that runs it under a private service UID.

## Public API

`from memory_engine import …`: `Commit`, `DatabaseInfo`, `DatabaseInfoContract`, `MemoryStore`, `Store`, `StoreError`, `Transaction`.

| Module | Purpose |
| --- | --- |
| [`memory_engine.service`](service.py) | Dedicated Memory DB Unix-socket service. Run under a private service UID. |

Command line:

- `python -m memory_engine.service`

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `memory_engine.MemoryStore` | `project_board` |
| `memory_engine.StoreError` | `project_board` |

## Ports

None. The unit imports only the standard library, third-party packages and its own modules.

## Install

- Nothing to wire: import the modules you need.
