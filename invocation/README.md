# `invocation`

The invocation lifecycle on native Kristall (create, start, finish and fail, with execution traces), the immutable request-scoped execution context and RFC 9457 Problem Details responses.

## Public API

`from invocation import …`: `InvocationContext`.

| Module | Purpose |
| --- | --- |
| [`invocation.api`](api.py) | REST helpers for persisted invocation lifecycle and traces. |
| [`invocation.manager`](manager.py) | Invocation lifecycle and trace persistence on native Kristall. |
| [`invocation.problems`](problems.py) | RFC 9457 Problem Details responses for the Logos API. |
| [`invocation.trace`](trace.py) | Invocation-scoped ExecutionTrace ownership helpers. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `invocation.api.get_invocation_status` | `mcp_server`, `runtime` |
| `invocation.api.get_invocation_trace` | `mcp_server`, `runtime` |
| `invocation.manager.create_invocation` | `agent_runtime`, `chat`, `mcp_server`, `runtime`, `tools.knowledge` |
| `invocation.manager.fail_invocation` | `agent_runtime`, `chat`, `mcp_server`, `tools.knowledge` |
| `invocation.manager.finish_invocation` | `agent_runtime`, `chat`, `mcp_server`, `tools.knowledge` |
| `invocation.manager.persist_invocation_trace` | `agent_runtime`, `chat`, `mcp_server`, `tools.knowledge` |
| `invocation.manager.start_invocation` | `agent_runtime`, `chat`, `mcp_server`, `tools.knowledge` |
| `invocation.problems.problem` | `web_shell` |
| `invocation.trace.create_invocation_trace` | `agent_runtime`, `chat`, `mcp_server`, `remote_runtime`, `tools.knowledge` |

## Ports

Everything `invocation` uses from other units is a field of `Host` in [`invocation/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `ExecutionTrace` | `observability.trace_manager.ExecutionTrace` |
| `KristallError` | `kristall.KristallError` |
| `KristallLimitError` | `kristall.KristallLimitError` |
| `MAX_JOURNAL_FRAME_BYTES` | `kristall.store.MAX_JOURNAL_FRAME_BYTES` |
| `create_session` | `sessions.manager.create_session` |
| `get_memory_client` | `agent_memory.client.get_memory_client` |
| `get_messages` | `storage.records.get_messages` |
| `memory_file_path` | `storage.records.memory_file_path` |
| `restore_session` | `sessions.manager.restore_session` |

## Install

- `composition/wiring.py` registers the `invocation` host factory (`register_all()`, or `register_units("invocation")` for this unit alone); the host is built on first use.
