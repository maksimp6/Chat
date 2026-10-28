# `agent_runtime`

The agent runtime: the provider-agnostic agent gateway, the persistent gateway for remote Local Tool Agents and its client, the headless GitHub issue runner, the launch hold, the read-only review policy and helpers for Yandex Responses tool loops.

## Public API

`from agent_runtime import …`: `cli`, `gateway`, `issue_runner`, `local_gateway`, `logos_launch_hold`.

| Module | Purpose |
| --- | --- |
| [`agent_runtime.cli`](cli.py) | Thin CLI/Termux adapter for the canonical Logos runtime. |
| [`agent_runtime.gateway`](gateway.py) | Provider-agnostic gateway for Logos agents. |
| [`agent_runtime.issue_runner`](issue_runner.py) | Run Logos headlessly on a GitHub issue against its own checkout. |
| [`agent_runtime.local_gateway`](local_gateway.py) | Persistent gateway for remote Local Tool Agents without application login. |
| [`agent_runtime.logos_launch_hold`](logos_launch_hold.py) | Enforce the owner's court remand before native/model-backed work starts. |

Command line:

- `python -m agent_runtime.cli`
- `python -m agent_runtime.issue_runner`
- `python -m agent_runtime.logos_launch_hold`

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `agent_runtime.gateway.AgentGateway` | `mcp_server` |
| `agent_runtime.issue_runner.DEFAULT_MODEL` | `agent_shell` |
| `agent_runtime.issue_runner.run_issue_task` | `agent_shell` |
| `agent_runtime.issue_runner.seed_provider_credential` | `tools.knowledge` |
| `agent_runtime.local_gateway.enqueue_local_tool_job` | `tool_providers` |
| `agent_runtime.local_gateway.wait_for_local_tool_job` | `tool_providers` |

## Ports

Everything `agent_runtime` uses from other units is a field of `Host` in [`agent_runtime/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

<details>
<summary>19 ports</summary>

| Port | Provided by |
| --- | --- |
| `Config` | `core.config.Config` |
| `DatabaseError` | `storage.memory_db.DatabaseError` |
| `LogosClient` | `chat.mcp_routes.LogosClient` |
| `UniversalToolCall` | `tool_providers.universal_tool_platform.UniversalToolCall` |
| `bootstrap_credential` | `provider_accounts.credentials.bootstrap_credential` |
| `create_invocation` | `invocation.manager.create_invocation` |
| `create_invocation_trace` | `invocation.trace.create_invocation_trace` |
| `current_runtime_data_root` | `runtime.request_context.current_runtime_data_root` |
| `current_runtime_id` | `runtime.request_context.current_runtime_id` |
| `decrypt_secret` | `secret_store.credential_crypto.decrypt_secret` |
| `encrypt_secret` | `secret_store.credential_crypto.encrypt_secret` |
| `fail_invocation` | `invocation.manager.fail_invocation` |
| `filesystem` | `tool_providers.filesystem` |
| `finish_invocation` | `invocation.manager.finish_invocation` |
| `get_store` | `storage.db.get_store` |
| `initialize_application` | `composition.application.initialize` |
| `persist_invocation_trace` | `invocation.manager.persist_invocation_trace` |
| `registry` | `tool_providers.registry.registry` |
| `start_invocation` | `invocation.manager.start_invocation` |

</details>

## Install

- `composition/wiring.py` registers the `agent_runtime` host factory (`register_all()`, or `register_units("agent_runtime")` for this unit alone); the host is built on first use.
- `composition/web.py` registers the blueprint `local_agent_bp` (`agent_runtime.local_gateway`).
- Storage start-up (`web_shell`) runs `agent_runtime.local_gateway.init_local_agent_tables`.
