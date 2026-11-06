# Ruvane

`runtime` · Scoped dispatcher for preview runtimes' database, file and tool access.

Scoped preview runtimes: the execution boundary (dispatcher), request-scoped runtime metadata, a small extension loader and invocation API helpers.

## Role in a dialog

Where this unit sits in the [life of a dialog](../docs/architecture/modules.md#life-of-a-dialog):

- **Trace, cost and treasury of a reply**: `GET /api/invocations/<invocation_id>/trace`, `GET /api/treasury/account`

## Public API

`from runtime import …`: `FILESYSTEM_READ_TEXT`, `FILESYSTEM_WRITE_TEXT`, `RuntimeContext`, `RuntimeDispatcher`, `RuntimeNotFound`, `RuntimeOperationNotFound`, `RuntimeOwnerViolation`, `RuntimeScopeViolation`, `RuntimeHostAPI`, `RuntimeLoadError`, `RuntimeLoader`, `bind_runtime_request`, `current_runtime_base_path`, `current_runtime_data_root`, `current_runtime_id`.

| Module | Purpose |
| --- | --- |
| [`runtime.api`](api.py) | Small API helpers for invocation lifecycle endpoints. |
| [`runtime.request_context`](request_context.py) | Request-scoped runtime metadata for the single-process preview host. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `runtime.RuntimeDispatcher` | `mcp_server`, `remote_runtime` |
| `runtime.RuntimeLoader` | `remote_runtime` |
| `runtime.RuntimeNotFound` | `mcp_server`, `remote_runtime` |
| `runtime.RuntimeOperationNotFound` | `mcp_server` |
| `runtime.RuntimeScopeViolation` | `mcp_server` |
| `runtime.current_runtime_base_path` | `web_shell` |
| `runtime.request_context.bind_runtime_request` | `remote_runtime` |
| `runtime.request_context.current_runtime_data_root` | `agent_runtime`, `storage` |
| `runtime.request_context.current_runtime_id` | `agent_runtime` |

## Ports

Everything `runtime` uses from other units is a field of `Host` in [`runtime/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `get_invocation_status` | `invocation.api.get_invocation_status` |
| `get_invocation_trace` | `invocation.api.get_invocation_trace` |
| `validate_api_contract` | `api.contracts.validate_api_contract` |

## Install

- `composition/wiring.py` registers the `runtime` host factory (`register_all()`, or `register_units("runtime")` for this unit alone); the host is built on first use.
- `composition/web.py` registers the blueprint `runtime_bp` (`runtime.api`).
