# `project_board`

Logos Board: private, SQL-free discussions, incidents, tasks and evidence on a durable append-only store, with a local HTTP API and an explicit seed.

## Public API

`from project_board import …`: `BoardConflict`, `BoardError`, `BoardStore`, `BoardUnavailable`.

| Module | Purpose |
| --- | --- |
| [`project_board.seed`](seed.py) | Explicit, idempotent seed of the confirmed missing-controls incident into Logos Board. |
| [`project_board.server`](server.py) | Local HTTP API for Logos Board without a sign-in requirement. |

Command line:

- `python -m project_board.seed`
- `python -m project_board.server`

## Ports

Everything `project_board` uses from other units is a field of `Host` in [`project_board/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `MemoryStore` | `memory_engine.MemoryStore` |
| `StoreError` | `memory_engine.StoreError` |

## Install

- `composition/wiring.py` registers the `project_board` host factory (`register_all()`, or `register_units("project_board")` for this unit alone); the host is built on first use.
