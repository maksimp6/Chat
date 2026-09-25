# ChatGPT Apps SDK / MCP

Alice Pro exposes a separate MCP endpoint for connection from ChatGPT. The MCP surface is conversation-first: ChatGPT can discover owned conversations, read conversation messages, and inspect the execution trace associated with those conversations. Project/diagnostic tools and the existing Git tool set remain available; Git write operations stay protected by the Universal Tool Executor approval boundary.

## Endpoint

The connector endpoint is:

`https://<public-host>/mcp`

The service uses MCP Streamable HTTP at `/mcp`. POST carries JSON-RPC messages; GET is available as an SSE stream for server-to-client notifications (currently a stateless keepalive stream because Alice Pro has no unsolicited MCP notifications); DELETE is idempotent because the endpoint does not create MCP sessions; OPTIONS exposes the transport headers for browser clients. The endpoint supports the current `2026-07-28` discovery flow and the `2025-11-25` and earlier handshake revisions.

MCP authentication is enforced unless local anonymous mode is explicitly enabled with `ALICE_MCP_ALLOW_ANONYMOUS=true`. For authenticated ChatGPT connections, tool descriptors advertise the OAuth 2.0 security scheme and the server exposes protected-resource metadata plus OAuth authorization-server discovery. Bearer mode remains available for private development/testing; introspection mode resolves the user ID from an RFC 7662-style introspection response.

The HTTP transport remains stateless. Each `tools/call` creates a short-lived Alice Pro `Invocation` with a persisted `ExecutionTrace`; the response returns the `invocation_id` and `trace_id` for correlation. This keeps MCP connection state out of the web process while retaining an auditable application-level execution record.

## Exposed tools

The MCP endpoint exposes the following audited tools through the Universal Tool Registry/Executor:

| Tool | Purpose | Permission |
| --- | --- | --- |
| `alice_list_conversations` | List conversations owned by the authenticated user | read-only |
| `alice_get_conversation` | Read one owned conversation | read-only |
| `alice_get_conversation_messages` | Read messages and persisted trace data for one owned conversation | read-only |
| `alice_get_execution` | Read one execution using the user-facing execution abstraction | read-only |
| `alice_get_execution_trace` | Read sanitized ExecutionTrace for one authenticated execution | read-only |
| `alice_get_system_status` | Runtime, MCP, model and local-tool status | read-only |
| `alice_list_agents` | Registered Agent Gateway agents | read-only |
| `alice_get_session` | Session lifecycle status | read-only |
| `alice_get_invocation` | Invocation status and safe metadata | read-only |
| `alice_get_invocation_trace` | Persisted ExecutionTrace | read-only |
| `alice_list_project_files` | List project files/directories | read-only |
| `alice_read_project_file` | Bounded file read | read-only |
| `alice_search_project` | Code/text search | read-only |
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

The Git functions are the existing implementations from `git_mcp_tools.py`; ChatGPT access adds no duplicate Git implementations. Any tool marked approval-required is rejected by `UniversalToolExecutor` until the existing Alice Pro approval flow supplies an approved call.

Tool results use MCP `structuredContent` plus text `content`. No custom widget is required for the initial integration.

## Security

Authentication is enforced at the MCP server boundary rather than delegated to the model.

For local/developer testing:

```env
ALICE_MCP_ALLOW_ANONYMOUS=true
```

For a private development deployment, a single bearer token can be configured:

This mode is intended for private/testing use and is **not** a replacement for ChatGPT OAuth.

For a production ChatGPT connection, configure an OAuth 2.1 resource-server flow backed by an external identity provider:

```env
ALICE_MCP_PUBLIC_URL=https://mcp.example.com
ALICE_MCP_OAUTH_ISSUER=https://auth.example.com
ALICE_MCP_OAUTH_SCOPE=alice.read
ALICE_MCP_INTROSPECTION_URL=https://auth.example.com/oauth2/introspect
ALICE_MCP_INTROSPECTION_CLIENT_ID=<id>
ALICE_MCP_INTROSPECTION_CLIENT_SECRET=<secret>
```

Alice Pro publishes:

`GET /.well-known/oauth-protected-resource` and `GET /.well-known/oauth-authorization-server`

The endpoint points ChatGPT at the configured authorization server and advertises the required scope. OAuth endpoint settings are resolved from the current process environment for each request.

Conversation data is protected by a dedicated `conversation_owners` table. An authenticated MCP principal can only enumerate and read conversations mapped to that principal. Execution and trace reads additionally enforce the existing invocation ownership metadata. The transport session itself remains stateless.

The application maintains the internal correlation chain:

`conversation_id -> session_id -> invocation_id -> trace_id`

External MCP clients should use conversation and execution identifiers; session identifiers remain an internal runtime detail.

Secrets must never be included in MCP tool metadata, results, ordinary logs, or ExecutionTrace.

## Connecting in ChatGPT

The production service must be reachable over HTTPS. In ChatGPT developer mode, create an app/connector using the public URL ending in `/mcp`.

For local development, expose the Flask service through a public HTTPS tunnel such as an approved development tunnel. Do not publish the Yandex API key or Supabase service-role credential.

## ChatGPT compatibility contract

Authenticated tools advertise `securitySchemes: [{"type": "oauth2", "scopes": ["alice.read"]}]`. Tool descriptors also expose standard MCP annotations such as `readOnlyHint` and `destructiveHint`. Authentication failures return a `WWW-Authenticate` resource-metadata challenge and the MCP authentication metadata needed for ChatGPT to start linking.

The authorization server must support OAuth 2.1 authorization-code + PKCE with S256 and echo the MCP resource through the authorization/token flow. Set `ALICE_MCP_OAUTH_AUTHORIZATION_URL` and `ALICE_MCP_OAUTH_TOKEN_URL` to the actual provider endpoints. Alice Pro does not implement the identity provider itself.

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
- approval enforcement for all existing Git write tools;
- Streamable HTTP GET/SSE, DELETE and CORS method/header compatibility.

## Next expansion

The next integration step is the production OAuth authorization server or trusted OAuth proxy, plus wiring the existing approval UI/API to approved MCP-originated write calls. This keeps Governance/Policy inside Alice Pro.
