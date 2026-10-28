# `remote_runtime`

Remote runtimes: the branch-aware application environment control plane and its REST API, and the SSH runtime (execution as configured Linux users, its settings policy and HTTP API).

## Public API

`from remote_runtime import …`: `environment_routes`, `environments`, `ssh`, `ssh_routes`, `ssh_settings`.

| Module | Purpose |
| --- | --- |
| [`remote_runtime.environment_routes`](environment_routes.py) | REST API for branch-aware application environments. |
| [`remote_runtime.environments`](environments.py) | Branch-aware application environment control plane. |
| [`remote_runtime.ssh`](ssh.py) | SSH-backed runtime for executing commands and files as configured Linux users. |
| [`remote_runtime.ssh_routes`](ssh_routes.py) | HTTP API of the SSH runtime: settings and a connection test. |
| [`remote_runtime.ssh_settings`](ssh_settings.py) | Configuration and policy boundary for the SSH Runtime plugin. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `remote_runtime.ssh.SSHRuntimeError` | `tool_providers` |
| `remote_runtime.ssh_settings.assert_operation_allowed` | `tool_providers` |
| `remote_runtime.ssh_settings.build_runtime` | `tool_providers` |

## Ports

Everything `remote_runtime` uses from other units is a field of `Host` in [`remote_runtime/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

<details>
<summary>14 ports</summary>

| Port | Provided by |
| --- | --- |
| `ExecutionTrace` | `observability.trace_manager.ExecutionTrace` |
| `RuntimeDispatcher` | `runtime.RuntimeDispatcher` |
| `RuntimeLoader` | `runtime.RuntimeLoader` |
| `RuntimeNotFound` | `runtime.RuntimeNotFound` |
| `TreasuryIdentityError` | `economy.treasury_identity.TreasuryIdentityError` |
| `bind_runtime_request` | `runtime.request_context.bind_runtime_request` |
| `close_memory_clients` | `agent_memory.client.close_memory_clients` |
| `create_invocation_trace` | `invocation.trace.create_invocation_trace` |
| `get_config` | `storage.db.get_config` |
| `get_current_owner_id` | `economy.treasury_identity.get_current_owner_id` |
| `get_current_trace` | `observability.trace_manager.get_current_trace` |
| `get_store` | `storage.db.get_store` |
| `init_runtime_db` | `storage.db.init_db` |
| `set_config` | `storage.db.set_config` |

</details>

## Install

- `composition/wiring.py` registers the `remote_runtime` host factory (`register_all()`, or `register_units("remote_runtime")` for this unit alone); the host is built on first use.
- `composition/web.py` registers the blueprints `environment_bp` (`remote_runtime.environment_routes`), `environment_gateway_bp` (`remote_runtime.environment_routes`).
- `composition/web.py` calls `remote_runtime.ssh_routes.install(app)`.
- Storage start-up (`web_shell`) runs `remote_runtime.environments.init_environment_tables`.
