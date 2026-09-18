# ChatGPT Apps SDK / MCP

Alice Pro exposes a separate, read-only MCP endpoint for connection from ChatGPT.

## Endpoint

The connector endpoint is:

`https://<public-host>/mcp`

The service uses MCP Streamable HTTP. The current implementation accepts protocol versions `2026-07-28`, `2025-06-18` and `2025-03-26`. Requests must include `MCP-Protocol-Version` and `Mcp-Method`; `tools/call` additionally requires `Mcp-Name` matching `params.name`.

No connection/session state is stored by the MCP transport. Alice Pro's existing runtime `Session`, `Invocation` and `ExecutionTrace` records remain the application-level state.

## Exposed tools

Only audited read-only bridge tools are exposed:

| Tool | Purpose |
| --- | --- |
| `alice_get_system_status` | Runtime, MCP, model and local-tool status |
| `alice_list_agents` | Registered Agent Gateway agents |
| `alice_get_session` | Session lifecycle status |
| `alice_get_invocation` | Invocation status and safe metadata |
| `alice_get_invocation_trace` | Persisted ExecutionTrace |

The internal local tool registry is intentionally **not** exposed wholesale. Write/destructive tools remain behind Alice Pro's existing approval and policy flow.

Tool results use MCP `structuredContent` plus text `content`. No custom widget is required for the initial integration.

## Security

Authentication is enforced at the MCP server boundary rather than delegated to the model.

For local/developer testing:

```env
ALICE_MCP_ALLOW_ANONYMOUS=true
```

For a private development deployment, a single bearer token can be configured:

```env
ALICE_MCP_BEARER_TOKEN=<secret>
ALICE_MCP_USER_ID=<stable-user-id>
```

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

`GET /.well-known/oauth-protected-resource`

The endpoint points ChatGPT at the configured authorization server and advertises the required scope.

Invocation and trace data are checked against the authenticated user ID when the invocation carries trusted ownership metadata. Legacy invocations without ownership are not exposed through authenticated mode.

Secrets must never be included in MCP tool metadata, results, ordinary logs, or ExecutionTrace.

## Connecting in ChatGPT

The production service must be reachable over HTTPS. In ChatGPT developer mode, create an app/connector using the public URL ending in `/mcp`.

For local development, expose the Flask service through a public HTTPS tunnel such as an approved development tunnel. Do not publish the Yandex API key or Supabase service-role credential.

## Validation

The contract is covered by `tests/test_chatgpt_mcp.py`, including:

- deterministic `tools/list`;
- structured `tools/call` results;
- standard MCP header validation;
- authentication failures and bearer authentication;
- user isolation for invocation and trace reads.

## Next expansion

The next integration step is a production OAuth authorization server or trusted OAuth proxy, followed by additional explicitly approved tools and optional MCP Apps UI resources. This keeps the Android shell lightweight and keeps Governance/Policy inside Alice Pro.
