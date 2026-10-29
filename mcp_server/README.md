# `mcp_server`

The Logos MCP server: protocol and transport, the shared local identity, records of MCP servers and their per-conversation selection, the ChatGPT bridge tools and the bridge to the runtime dispatcher.

## Role in a dialog

Where this unit sits in the [life of a dialog](../docs/architecture/modules.md#life-of-a-dialog):

- **Tools during the turn**: `POST /api/chat`, `GET /api/mcp-servers`
- **Read dialogs from outside (MCP)**: `POST {MCP_PATH}`

## Public API

`from mcp_server import …`: `MCP_PATH`, `SUPPORTED_PROTOCOL_VERSIONS`, `DEFAULT_PROTOCOL_VERSION`, `SERVER_NAME`, `SERVER_VERSION`, `OAUTH_SCOPE`, `PUBLIC_BASE_URL`, `registry`, `mcp_runtime_dispatcher`, `chatgpt_mcp_bp`, `mcp_options`, `mcp_get`, `mcp_post`.

| Module | Purpose |
| --- | --- |
| [`mcp_server.storage`](storage.py) | Native shared MCP server records and per-conversation selection. |
| [`mcp_server.tools`](tools.py) | Tools of the Logos MCP server: system status, agents, sessions, invocations and traces, conversations, and the ChatGPT project, conversation and bridge tools. |
| [`mcp_server.trace`](trace.py) | Trace correlation helpers for MCP activity managed by Yandex AI Studio. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `mcp_server.storage` | `chat`, `yandex_client_modules` |
| `mcp_server.trace.record_yandex_mcp_activity` | `chat` |

## Ports

Everything `mcp_server` uses from other units is a field of `Host` in [`mcp_server/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

<details>
<summary>31 ports</summary>

| Port | Provided by |
| --- | --- |
| `ALL_MODELS` | `core.config.ALL_MODELS` |
| `AgentGateway` | `agent_runtime.gateway.AgentGateway` |
| `CredentialCryptoError` | `secret_store.credential_crypto.CredentialCryptoError` |
| `RuntimeDispatcher` | `runtime.RuntimeDispatcher` |
| `RuntimeNotFound` | `runtime.RuntimeNotFound` |
| `RuntimeOperationNotFound` | `runtime.RuntimeOperationNotFound` |
| `RuntimeScopeViolation` | `runtime.RuntimeScopeViolation` |
| `UniversalToolCall` | `tool_providers.universal_tool_platform.UniversalToolCall` |
| `bind_current_trace` | `observability.trace_manager.bind_current_trace` |
| `check_access` | `conversations.ownership.check_access` |
| `create_invocation` | `invocation.manager.create_invocation` |
| `create_invocation_trace` | `invocation.trace.create_invocation_trace` |
| `decrypt_secret` | `secret_store.credential_crypto.decrypt_secret` |
| `encrypt_secret` | `secret_store.credential_crypto.encrypt_secret` |
| `fail_invocation` | `invocation.manager.fail_invocation` |
| `finish_invocation` | `invocation.manager.finish_invocation` |
| `get_current_owner_id` | `economy.treasury_identity.get_current_owner_id` |
| `get_invocation_status` | `invocation.api.get_invocation_status` |
| `get_invocation_trace` | `invocation.api.get_invocation_trace` |
| `get_messages` | `storage.records.get_messages` |
| `get_owned_conversation` | `conversations.ownership.get_owned_conversation` |
| `get_session` | `sessions.manager.get_session` |
| `get_store` | `storage.records.get_store` |
| `grep_search` | `tool_providers.filesystem.grep_search` |
| `list_directory` | `tool_providers.filesystem.list_directory` |
| `list_owned_conversations` | `conversations.ownership.list_owned_conversations` |
| `persist_invocation_trace` | `invocation.manager.persist_invocation_trace` |
| `read_file` | `tool_providers.filesystem.read_file` |
| `registry` | `tool_providers.registry.registry` |
| `reset_current_trace` | `observability.trace_manager.reset_current_trace` |
| `start_invocation` | `invocation.manager.start_invocation` |

</details>

## Install

- `composition/wiring.py` registers the `mcp_server` host factory (`register_all()`, or `register_units("mcp_server")` for this unit alone); the host is built on first use.
- `composition/web.py` registers the blueprint `chatgpt_mcp_bp` (`mcp_server`).
- `composition/web.py` calls `mcp_server.tools.register_mcp_tools()`.
- Storage start-up (`web_shell`) runs `mcp_server.storage.ensure_rdc_server`: the Remote Desktop Commander server (`https://mcp.desktopcommander.app/mcp`, label `rdc`) is created once without a token; it reaches the provider only after the owner puts `Bearer <token>` into its authorization in the MCP panel.
