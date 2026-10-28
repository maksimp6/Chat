# `sessions`

Durable runtime sessions stored as Kristall journal records, built-in reusable AI session profiles and the managed lightweight runtimes behind them.

## Public API

`from sessions import …`: `manager`, `profiles`.

| Module | Purpose |
| --- | --- |
| [`sessions.manager`](manager.py) | Durable runtime sessions stored directly as Kristall journal records. |
| [`sessions.profiles`](profiles.py) | Built-in reusable AI session profiles. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `sessions.manager.create_session` | `invocation`, `organization`, `runtime` |
| `sessions.manager.get_session` | `mcp_server`, `organization`, `runtime` |
| `sessions.manager.restore_session` | `invocation` |
| `sessions.profiles.clone_profile` | `runtime` |
| `sessions.profiles.get_profile` | `runtime` |
| `sessions.profiles.list_profiles` | `runtime` |
| `sessions.profiles.session_profile` | `runtime` |

## Ports

Everything `sessions` uses from other units is a field of `Host` in [`sessions/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `get_memory_client` | `agent_memory.client.get_memory_client` |
| `memory_file_path` | `storage.db.memory_file_path` |

## Install

- `composition/wiring.py` registers the `sessions` host factory (`register_all()`, or `register_units("sessions")` for this unit alone); the host is built on first use.
