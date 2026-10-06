# ChatGPT Browser Tool contract

Issue: #751

This document defines the external tool boundary for the persistent Chrome Worker.
It is intentionally transport-neutral so the same browser operations can be
exposed to ChatGPT through an MCP/plugin adapter and to Alice Pro.

## Request path

```
ChatGPT -> OAuth-authenticated MCP client -> Cloud.ru API Gateway -> Playwright MCP -> Chrome Worker
Alice   -> authenticated client -> Cloud.ru API Gateway -> Playwright API -> Chrome Worker
```

The Gateway is the supported public boundary. The worker must not expose CDP,
Chrome profile files, cookies or credentials as separate HTTP endpoints. The
MCP endpoint uses the standard official Playwright MCP tool set.

## Stable operations

- `browser_status`: readiness, session identifier and safe page metadata.
- `wake` / `sleep`: start Chrome on the persistent profile or close it cleanly
  before Container Apps scales the worker to zero.
- `navigate`: navigate the active page to an allowed HTTP(S) URL.
- `click`: click a locator in the active page.
- `type`: enter text into a locator. Secret values must be supplied through a
  protected credential mechanism, never ordinary tool arguments or traces.
- `extract`: return bounded structured/text page content.
- `screenshot`: return a bounded screenshot artifact/reference.

Each operation carries a generated request ID. Alice-originated calls may also
carry an ExecutionTrace correlation ID. Neither identifier is authorization.

## Authentication boundary

The Gateway exposes the dedicated HTTPS origin under `maxxxpavlov.online` and
routes only the declared paths through the native Container Apps backend.
Direct container ingress retains provider authentication.
The Gateway must preserve application Authorization, MCP session/protocol headers,
OAuth query parameters and cookies. The worker validates OAuth access tokens
for ChatGPT's MCP calls and a machine bearer token for operations clients.
They do not inherit Alice's browser cookie merely because Alice and the browser
worker share a project. ChatGPT and Alice are distinct clients.

OAuth discovery, dynamic client registration and GitHub sign-in are routed on
the same public origin. Authorization requires PKCE S256 and the configured
GitHub owner account. OAuth access cannot call the lifecycle REST routes.
Credentials are never returned by a browser operation. Gateway logs, worker logs,
Actions output and ExecutionTrace
must redact Authorization, Cookie, Set-Cookie, Google account data and extension
secrets.

## Chrome boundary

Playwright controls the installed Google Chrome Stable executable. CDP listens
only inside the worker and is never routed by API Gateway. The persistent Chrome
profile is private worker state. Extensions are installed from an explicit
allowlist/configuration and must not rely on Chrome Sync as their sole recovery
path.

## Initial HTTP mapping

The concrete API may evolve before production, but the first implementation
should keep a small versioned namespace:

- `GET /browser/v1/status`
- `POST /browser/v1/wake`
- `POST /browser/v1/sleep`
- `POST /browser/v1/navigate`
- `POST /browser/v1/click`
- `POST /browser/v1/type`
- `POST /browser/v1/extract`
- `POST /browser/v1/screenshot`
- `POST /browser/v1/mcp` (official Playwright MCP Streamable HTTP)
- `GET /browser/v1/mcp` (MCP event stream)
- `DELETE /browser/v1/mcp` (MCP session termination)

The MCP tool names are the official `browser_*` names, such as
`browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, and
`browser_take_screenshot`. The short names above describe the retained REST
operations. See `deploy/chrome-worker/README.md` for client configuration.

Do not add a catch-all browser proxy. Every externally callable browser operation
must be explicitly declared in the Gateway contract and covered by authorization,
input bounds, rate limits and tests.

## OAuth flows

The Chrome Worker and API Gateway support ChatGPT and third-party OAuth flows
for headless and persistent browser operations. OAuth is handled by
`deploy/chrome-worker/oauth.mjs` and validated at each browser MCP call.

### Supported OAuth scopes

- `browser`: core Playwright MCP operations
- `offline_access`: long-lived refresh tokens for ChatGPT and API integrations

Both scopes are required; the worker normalizes requests to exactly `browser offline_access`.

### ChatGPT offline token flow

When ChatGPT requests `offline_access`:

1. Owner authenticates once with GitHub sign-in at `{origin}/browser/oauth`
2. OAuth endpoint issues refresh_token (valid 30 days) and access_token (15 min TTL)
3. Client stores refresh_token for reuse across sessions
4. On expiry, client exchanges refresh_token for new access_token without user interaction
5. Offline operation continues: Chrome persists, profile retained, no interactive sign-in needed

Implementation details (`deploy/chrome-worker/oauth.mjs`):
- Refresh tokens are issued with 30-day validity and stored on the client
- Access tokens expire in 15 minutes; expired tokens trigger automatic refresh
- Unconfirmed OAuth clients (dynamic registration) are evicted after 7 days if not confirmed by owner
- Confirmed clients (after GitHub sign-in) are never evicted and support long-lived refresh
- Maximum 1000 provisional clients before eviction of oldest unconfirmed; prevents registration spam

Tests in `deploy/chrome-worker/oauth.test.mjs` verify offline flow end-to-end
including token refresh and revocation handling.

### Short-token (machine) authentication

Alice Pro's own browser operations use the `ALICE_SHORT_TOKEN` (or `BROWSER_API_TOKEN`)
bearer token for direct worker calls, bypassing OAuth. This skips the interactive
flow and is suitable for automated Playwright operations.

## First acceptance flow

1. External MCP/plugin client initializes a session and lists tools through API Gateway.
2. It navigates Chrome to a synthetic test page.
3. It clicks/types into non-secret controls.
4. It extracts bounded page content.
5. It obtains a screenshot reference.
6. A controlled worker restart restores the same private profile.
7. Direct anonymous access to worker control routes fails.
8. Logs/traces contain no cookies, credentials or profile data.
9. (Offline flow) Client stores refresh token and uses it to obtain new access tokens on expiry.
