# `tool_providers`

The tool platform: provider-agnostic tool contracts and the execution pipeline, the shared tool registry, and the built-in tool sources (git, Termux, system, filesystem, Wikipedia, profiler, theme, SSH runtime, PTY terminal sessions, cache) with JSON Schema helpers and the read-only policy.

## Role in a dialog

Where this unit sits in the [life of a dialog](../docs/architecture/modules.md#life-of-a-dialog):

- **Dialog settings and tools**: `GET /api/conversations/<conv_id>/settings`, `PUT /api/conversations/<conv_id>/settings`, `GET /api/tools/categories`, `PUT /api/conversations/<conv_id>/tools`
- **Tools during the turn**: `POST /api/chat`, `GET /api/mcp-servers`, `POST /api/mcp-servers`
- **Approve a tool action**: `POST /api/mcp/execute-approved`
- **Read dialogs from outside (MCP)**: `POST {MCP_PATH}`

## Public API

`from tool_providers import …`: `filesystem`, `registry`, `runtime_tools`, `tty`, `universal_tool_platform`.

| Module | Purpose |
| --- | --- |
| [`tool_providers.filesystem`](filesystem.py) | Инструменты стандартной библиотеки Python и файловой системы для Logos. Включает: |
| [`tool_providers.registry`](registry.py) | Provider-agnostic registry for Logos tools. |
| [`tool_providers.runtime_tools`](runtime_tools.py) | Universal tool definitions backed by the SSH Runtime. |
| [`tool_providers.rootagent`](rootagent.py) | Logos tools `rootagent_run` and `rootagent_result` for the Redmi 9 RootAgent job on GitHub Actions. Registered as the `rootagent` external tool source through `composition/tool_sources.py`; not in `__all__`. |
| [`tool_providers.cache`](cache.py) | The cache (category `cache`): one long text that only grows (`logos.cache.md` beside the memory file); cache_append, cache_read (whole text only), cache_search. Each appended piece is embedded with every embedding model, each vector kept with its model version; after every append the whole text is uploaded as one file to the knowledge vector stores (the index File Search searches), replacing the previous upload. cache_search is a helper model (DeepSeek V4.1 Flash) that reads the whole text and answers the query. Not in `__all__`. |
| [`tool_providers.tty`](tty.py) | PTY terminal sessions (category `tty`): tty_open, tty_write, tty_read, tty_resize, tty_close, tty_list. Owner-checked; opening needs the owner's approval. Not yet verified on the Redmi (Termux). |
| [`tool_providers.universal_tool_platform`](universal_tool_platform.py) | Provider-agnostic tool contracts and execution pipeline for Logos. |

Other units reach it only through their own ports:

| Provides | To |
| --- | --- |
| `tool_providers.filesystem` | `agent_runtime`, `tools.knowledge` |
| `tool_providers.filesystem.grep_search` | `mcp_server` |
| `tool_providers.filesystem.list_directory` | `mcp_server` |
| `tool_providers.filesystem.read_file` | `mcp_server` |
| `tool_providers.filesystem.resolve_tool_path` | `browser` |
| `tool_providers.filesystem.resolves_to_link_local` | `browser` |
| `tool_providers.registry.registry` | `agent_runtime`, `chat`, `mcp_server`, `tools.knowledge`, `yandex_client_modules` |
| `tool_providers.runtime_tools.ssh_runtime_exec` | `cloud` |
| `tool_providers.universal_tool_platform.UniversalToolCall` | `agent_runtime`, `chat`, `mcp_server`, `plugin_system`, `runtime`, `tools.knowledge`, `yandex_client_modules` |
| `tool_providers.universal_tool_platform.UniversalToolExecutor` | `agent_runtime`, `chat`, `mcp_server`, `plugin_system`, `runtime`, `tools.knowledge`, `yandex_client_modules` |
| `tool_providers.universal_tool_platform.tool_call_requires_approval` | `mcp_server` |
| `tool_providers.universal_tool_platform.validate_json_schema` | `organization`, `tools.knowledge` |
| `tool_providers.universal_tool_platform.warn_deprecated_execution_hooks` | `runtime` |

## Ports

Everything `tool_providers` uses from other units is a field of `Host` in [`tool_providers/host.py`](host.py), read only when a function runs. Functions keep their names as call shims; other ports are `bound.<name>`.

| Port | Provided by |
| --- | --- |
| `Config` | `core.config.Config` |
| `EMBEDDING_MODELS` | `core.config.EMBEDDING_MODELS` |
| `EmbeddingError` | `yandex_client_modules.embeddings.EmbeddingError` |
| `KristallError` | `kristall.KristallError` |
| `SSHRuntimeError` | `remote_runtime.ssh.SSHRuntimeError` |
| `build_runtime` | `remote_runtime.ssh_settings.build_runtime` |
| `YandexClientError` | `yandex_client_modules.errors.YandexClientError` |
| `YandexResponsesClient` | `yandex_client_modules.client.YandexResponsesClient` |
| `embed_texts` | `yandex_client_modules.embeddings.embed_texts` |
| `enqueue_local_tool_job` | `agent_runtime.local_gateway.enqueue_local_tool_job` |
| `external_tool_sources` | `composition.tool_sources.SOURCES` |
| `knowledge_vector_store_ids` | `yandex_client_modules.tool_config.knowledge_vector_store_ids` |
| `memory_file_path` | `storage.records.memory_file_path` |
| `wait_for_local_tool_job` | `agent_runtime.local_gateway.wait_for_local_tool_job` |

## Install

- `composition/wiring.py` registers the `tool_providers` host factory (`register_all()`, or `register_units("tool_providers")` for this unit alone); the host is built on first use.
