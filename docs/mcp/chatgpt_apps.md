# ChatGPT Apps SDK / MCP

Logos exposes a separate MCP endpoint for connection from ChatGPT. The MCP surface is conversation-first: ChatGPT can discover owned conversations, read conversation messages, and inspect the execution trace associated with those conversations. Project/diagnostic tools and the existing Git tool set remain available; Git write operations stay protected by the Universal Tool Executor approval boundary.

## Endpoint

The connector endpoint is:

`https://<public-host>/mcp`

The service uses MCP Streamable HTTP. The endpoint supports the current `2026-07-28` discovery flow and the `2025-06-18` / `2025-03-26` handshake flow. Modern clients use `server/discover`; legacy clients can use `initialize`. The implementation accepts the `MCP-Protocol-Version` header when present and can default to the latest supported revision for local/compatibility clients.

MCP authentication is enforced unless local anonymous mode is explicitly enabled with `LOGOS_MCP_ALLOW_ANONYMOUS=true`. Bearer mode uses `LOGOS_MCP_BEARER_TOKEN` plus `LOGOS_MCP_USER_ID`; introspection mode resolves the user ID from an RFC 7662-style introspection response.

The HTTP transport remains stateless. Each `tools/call` creates a short-lived Logos `Invocation` with a persisted `ExecutionTrace`; the response returns the `invocation_id` and `trace_id` for correlation. This keeps MCP connection state out of the web process while retaining an auditable application-level execution record.

## Runtime-scoped calls

Clients targeting a preview runtime put `logos/runtime_id` in the `tools/call`
`params._meta` object. If the resource is runtime-owned, they also provide
`logos/resource_runtime_id`. Logos validates the authenticated owner and equal
caller/resource scopes through `RuntimeDispatcher` before the existing
`UniversalToolExecutor` boundary runs the tool. Missing connectors, owner
mismatches, and cross-runtime attempts return the same credential-free error;
the correlated `ExecutionTrace` records the violation category for operators.

These fields carry opaque identifiers, never credentials. Clients cannot
select or enumerate an unrestricted tool registry through this contract.

## Exposed tools

The MCP endpoint exposes the following audited tools through the Universal Tool Registry/Executor:

| Tool | Purpose | Permission |
| --- | --- | --- |
| `logos_list_conversations` | List conversations owned by the authenticated user | read-only |
| `logos_get_conversation` | Read one owned conversation | read-only |
| `logos_get_conversation_messages` | Read messages and persisted trace data for one owned conversation | read-only |
| `logos_get_execution` | Read one execution using the user-facing execution abstraction | read-only |
| `logos_get_execution_trace` | Read sanitized ExecutionTrace for one authenticated execution | read-only |
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
| `git_add` | Stage changes | approval required |
| `git_commit` | Create commit | approval required |
| `git_remote` | Read/change remotes | approval required |
| `git_push` | Push changes | approval required |
| `git_pull` | Pull and merge changes | approval required |
| `git_fetch` | Fetch remote changes | approval required |

The Git functions are the existing implementations from `tool_providers/git.py`; ChatGPT access adds no duplicate Git implementations. Any tool marked approval-required is rejected by `UniversalToolExecutor` until the existing Logos approval flow supplies an approved call.

Tool results use MCP `structuredContent` plus text `content`. No custom widget is required for the initial integration.

## Security

Authentication is enforced at the MCP server boundary rather than delegated to the model.

For local/developer testing:

```env
LOGOS_MCP_ALLOW_ANONYMOUS=true
```

For a private development deployment, a single bearer token can be configured:

This mode is intended for private/testing use and is **not** a replacement for ChatGPT OAuth.

For a production ChatGPT connection, configure an OAuth 2.1 resource-server flow backed by an external identity provider:

```env
LOGOS_MCP_PUBLIC_URL=https://mcp.example.com
LOGOS_MCP_OAUTH_ISSUER=https://auth.example.com
LOGOS_MCP_OAUTH_SCOPE=logos.read
LOGOS_MCP_AUDIENCE=https://mcp.example.com/mcp
LOGOS_MCP_INTROSPECTION_URL=https://auth.example.com/oauth2/introspect
LOGOS_MCP_INTROSPECTION_CLIENT_ID=<id>
LOGOS_MCP_INTROSPECTION_CLIENT_SECRET=<secret>
```

`LOGOS_MCP_AUDIENCE` is the canonical URL of this MCP endpoint (RFC 8707). Introspection refuses every token whose `aud` does not list it, and refuses to run without it.

Logos publishes:

`GET /.well-known/oauth-protected-resource`

The endpoint points ChatGPT at the configured authorization server and advertises the required scope.

Conversation data is protected by a dedicated `conversation_owners` table. An authenticated MCP principal can only enumerate and read conversations mapped to that principal. Execution and trace reads additionally enforce the existing invocation ownership metadata. The transport session itself remains stateless.

The application maintains the internal correlation chain:

`conversation_id -> session_id -> invocation_id -> trace_id`

External MCP clients should use conversation and execution identifiers; session identifiers remain an internal runtime detail.

Secrets must never be included in MCP tool metadata, results, ordinary logs, or ExecutionTrace.

## Connecting in ChatGPT

The production service must be reachable over HTTPS. In ChatGPT developer mode, create an app/connector using the public URL ending in `/mcp`.

For local development, expose the Flask service through a public HTTPS tunnel such as an approved development tunnel. Do not publish provider API keys or backend-only credentials.

### Through OpenAI Secure MCP Tunnel

The container entrypoint (`scripts/container_entrypoint.py`) can run
[openai/tunnel-client](https://github.com/openai/tunnel-client) next to Logos.
The client polls OpenAI over outbound HTTPS and forwards ChatGPT's MCP requests
to `http://127.0.0.1:$PORT/mcp`, adding `Authorization: Bearer
$LOGOS_MCP_BEARER_TOKEN` itself, so the connector in ChatGPT needs no
authentication of its own. The tunnel starts only when all of
`OPENAI_TUNNEL_ID`, `OPENAI_TUNNEL_API_KEY`, `LOGOS_MCP_BEARER_TOKEN` and
`LOGOS_MCP_USER_ID` are set; a partial set stops the container. Each process
gets only its own secrets, and if either exits the container exits.

Setup:

1. Create a tunnel and a runtime API key at
   `https://platform.openai.com/settings/organization/tunnels` (needs Tunnels
   Read + Manage + Use) and associate the tunnel with your ChatGPT workspace.
2. Store them as `OPENAI_TUNNEL_ID` and `OPENAI_TUNNEL_API_KEY` secrets of the
   `production` GitHub environment.
3. Run the **Cloud.ru OpenAI tunnel** workflow (`cloudru-openai-tunnel.yml`)
   with action `deploy`. It deploys the `logos-openai-tunnel` service with one
   always-on instance and generates the internal MCP bearer token per deploy.
4. In ChatGPT: Plugins, **+**, **Add custom MCP server**, Connection
   **Tunnel**, pick the tunnel.

The pinned client is v0.0.14 (`tunnel-client-runtime`, linux-amd64), verified
by SHA-256 in the `Dockerfile`.

## Validation

The contract is covered by `tests/test_chatgpt_mcp.py`, including:

- deterministic `tools/list`;
- structured `tools/call` results;
- legacy `initialize` handshake compatibility;
- persisted ExecutionTrace correlation for MCP tool calls;
- standard MCP header validation;
- authentication failures and bearer authentication;
- user isolation for conversations, invocation and trace reads;
- project tool execution through MCP;
- all existing Git read tools through MCP;
- approval enforcement for all existing Git write tools.

## Next expansion

The next integration step is the production OAuth authorization server or trusted OAuth proxy, plus wiring the existing approval UI/API to approved MCP-originated write calls. This keeps Governance/Policy inside Logos.
