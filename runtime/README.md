# `runtime`

Scoped preview runtimes: the execution boundary (dispatcher), request-scoped runtime metadata, the runtime storage capability, a small extension loader, conversation-scoped agent selection and session/invocation API helpers.

## Role in a dialog

Where this unit sits in the [life of a dialog](../docs/architecture/modules.md#life-of-a-dialog):

- **Trace, cost and treasury of a reply**: `GET /api/invocations/<invocation_id>/trace`, `GET /api/treasury/account`

## Public API

`from runtime import …`: `FILESYSTEM_READ_TEXT`, `FILESYSTEM_WRITE_TEXT`, `TOOL_EXECUTE_OPERATION`, `RuntimeContext`, `RuntimeDispatcher`, `RuntimeNotFound`, `RuntimeOperationNotFound`, `RuntimeOwnerViolation`, `RuntimeScopeViolation`, `RuntimeHostAPI`, `RuntimeLoadError`, `RuntimeLoader`, `ConversationAgent`, `ConversationAgentRouter`, `RuntimeStorage`, `bind_runtime_request`, `current_runtime_base_path`, `current_runtime_data_root`, `current_runtime_id`.

| Module | Purpose |
| --- | --- |
| [`runtime.api`](api.py) | Small API helpers for session/invocation lifecycle endpoints. |
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

<details>
<summary>17 ports</summary>

| Port | Provided by |
| --- | --- |
| `ContractViolation` | `api.contracts.ContractViolation` |
| `StorageCredentialsMissing` | `storage.contracts.StorageCredentialsMissing` |
| `StorageError` | `storage.contracts.StorageError` |
| `StorageProviderUnavailable` | `storage.contracts.StorageProviderUnavailable` |
| `UniversalToolCall` | `tool_providers.universal_tool_platform.UniversalToolCall` |
| `check_access` | `conversations.ownership.check_access` |
| `clone_profile` | `sessions.profiles.clone_profile` |
| `create_invocation` | `invocation.manager.create_invocation` |
| `create_session` | `sessions.manager.create_session` |
| `get_current_owner_id` | `economy.treasury_identity.get_current_owner_id` |
| `get_invocation_status` | `invocation.api.get_invocation_status` |
| `get_invocation_trace` | `invocation.api.get_invocation_trace` |
| `get_profile` | `sessions.profiles.get_profile` |
| `get_session` | `sessions.manager.get_session` |
| `list_profiles` | `sessions.profiles.list_profiles` |
| `session_profile` | `sessions.profiles.session_profile` |
| `validate_api_contract` | `api.contracts.validate_api_contract` |

</details>

## Install

- `composition/wiring.py` registers the `runtime` host factory (`register_all()`, or `register_units("runtime")` for this unit alone); the host is built on first use.
- `composition/web.py` registers the blueprint `runtime_bp` (`runtime.api`).
