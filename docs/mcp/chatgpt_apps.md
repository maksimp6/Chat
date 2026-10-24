# ChatGPT Apps SDK / MCP

Logos exposes a separate MCP endpoint for connection from ChatGPT. The MCP surface is conversation-first: ChatGPT can discover local conversations, read their messages, and inspect the associated execution traces. Project/diagnostic tools and the existing Git tool set remain available without a sign-in or approval gate.

## Endpoint

The connector endpoint is:

`https://<public-host>/mcp`

The service uses MCP Streamable HTTP. The endpoint supports the current `2026-07-28` discovery flow and the `2025-06-18` / `2025-03-26` handshake flow. Modern clients use `server/discover`; legacy clients can use `initialize`. The implementation accepts the `MCP-Protocol-Version` header when present and can default to the latest supported revision for local/compatibility clients.

MCP is available without authentication. It uses the same installation identity as the web application (`LOGOS_OWNER_ID`, or `default`). Old bearer and introspection settings do not enable a login gate.

The HTTP transport remains stateless. Each `tools/call` creates a short-lived Logos `Invocation` with a persisted `ExecutionTrace`; the response returns the `invocation_id` and `trace_id` for correlation. This keeps MCP connection state out of the web process while retaining an auditable application-level execution record.

## Runtime-scoped calls

Clients targeting a preview runtime put `logos/runtime_id` in the `tools/call`
`params._meta` object. If the resource is runtime-owned, they also provide
`logos/resource_runtime_id`. `RuntimeDispatcher` resolves the registered runtime
without a visitor ownership check and requires equal caller/resource runtime
scopes before `UniversalToolExecutor` runs the tool. Missing connectors and
cross-runtime attempts return a credential-free error; the correlated
`ExecutionTrace` records the violation category for operators.

These fields carry opaque identifiers, never credentials. Clients cannot
select or enumerate an unrestricted tool registry through this contract.

## Exposed tools

The MCP endpoint exposes the following audited tools through the Universal Tool Registry/Executor:

| Tool | Purpose | Permission |
| --- | --- | --- |
| `logos_list_conversations` | List local conversations | read-only |
| `logos_get_conversation` | Read one local conversation | read-only |
| `logos_get_conversation_messages` | Read messages and persisted trace data for one local conversation | read-only |
| `logos_get_execution` | Read one execution using the user-facing execution abstraction | read-only |
| `logos_get_execution_trace` | Read sanitized ExecutionTrace for one local execution | read-only |
| `logos_get_system_status` | Runtime, MCP, model and local-tool status | read-only |
| `logos_list_agents` | Registered Agent Gateway agents | read-only |
| `logos_get_session` | Session lifecycle status | read-only |
| `logos_get_invocation` | Invocation status and safe metadata | read-only |
| `logos_get_invocation_trace` | Persisted ExecutionTrace | read-only |
| `logos_list_project_files` | List project files/directories | read-only |
| `logos_read_project_file` | Bounded file read | read-only |
| `logos_search_project` | Code/text search | read-only |
| `git_status` | Repository status | read-only |
| `git_log` | Commit history | read-only |
| `git_diff` | Current diff | read-only |
| `git_branches` | Branch list | read-only |
| `git_add` | Stage changes | available |
| `git_commit` | Create commit | available |
| `git_remote` | Read/change remotes | available |
| `git_push` | Push changes | available |
| `git_pull` | Pull and merge changes | available |
| `git_fetch` | Fetch remote changes | available |

The Git functions are the existing implementations from `tool_providers/git.py`; ChatGPT access adds no duplicate Git implementations. Tool execution uses the existing `UniversalToolExecutor` without an approval gate.

Tool results use MCP `structuredContent` plus text `content`. No custom widget is required for the initial integration.

## Access and data

The MCP transport has no bearer-token or OAuth authorization checks and does not
publish OAuth protected-resource metadata. Tool descriptors declare `noauth`.
Conversations, sessions and execution history are shared across clients of this
installation. Provider credentials and trace sanitization remain separate from
visitor access.

The internal correlation chain remains:

`conversation_id -> session_id -> invocation_id -> trace_id`

Secrets must never be included in MCP tool metadata, results, ordinary logs, or
ExecutionTrace.

## Connecting in ChatGPT

The production service must be reachable over HTTPS. In ChatGPT developer mode, create an app/connector using the public URL ending in `/mcp`.

For local development, expose the Flask service through a public HTTPS tunnel such as an approved development tunnel. Do not publish provider API keys or backend-only credentials.

## Validation

The contract is covered by `tests/test_chatgpt_mcp.py`, including:

- deterministic `tools/list`;
- structured `tools/call` results;
- legacy `initialize` handshake compatibility;
- persisted ExecutionTrace correlation for MCP tool calls;
- standard MCP header validation;
- access with absent or invalid bearer headers and obsolete auth settings;
- shared local conversations, invocation and trace reads;
- project tool execution through MCP;
- all existing Git read tools through MCP;
- Git write dispatch through stubbed handlers without invoking Git.
