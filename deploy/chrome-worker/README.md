# Chrome + Playwright MCP

The worker runs official Google Chrome Stable with the persistent profile at
`/state/profile`. Mount the private state volume there. Chrome's sandbox is
explicitly disabled (`chromiumSandbox: false`). The Dockerfile retains the
original Russian CA installation and certificate files.

## MCP connection

`/browser/v1/mcp` runs the official `@playwright/mcp` server over MCP Streamable
HTTP, using the MCP SDK transport. It supports POST (initialize, notifications and
tool calls), GET (event stream), and DELETE (end session). Every request requires
`Authorization: Bearer` with an OAuth access token or `BROWSER_API_TOKEN`, including
requests carrying an `Mcp-Session-Id`. The Gateway must forward this session header,
Authorization, the MCP protocol version header and Accept header, and return
`Mcp-Session-Id` and `WWW-Authenticate` to the client. The OAuth routes also require
query strings, Cookie and Set-Cookie to pass through unchanged.

The advertised tools are Playwright MCP's standard tools, including
`browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, and
`browser_take_screenshot`. This replaces the earlier hand-written JSON-RPC
adapter. A tool call starts Chrome on demand. MCP clients share the worker's
browser context and profile; they are not isolated browser users. MCP sessions
are process-local, so keep a single worker replica or use sticky routing.

The existing REST operations remain available, including
`POST /browser/v1/sleep` and `POST /browser/v1/wake`. Sleep closes Chrome and
flushes its profile. The next MCP browser operation wakes it again. The optional
`BROWSER_ALLOWED_HOSTS` setting belongs to the legacy REST navigate operation;
it does not restrict the official MCP tools.

Use the Gateway's actual deployed URL with path `/browser/v1/mcp` in your client.
For a locally running worker, the Codex configuration is:

```toml
[mcp_servers.playwright]
url = "http://127.0.0.1:8080/browser/v1/mcp"
bearer_token_env_var = "BROWSER_API_TOKEN"
```

Set the token through the client's environment/secret store. In a remote setup,
replace the local URL with the deployed Gateway endpoint. No live Gateway URL
or credentials are embedded in this repository.

## Run and check

```bash
npm ci --prefix deploy/chrome-worker
npm start --prefix deploy/chrome-worker
```

Set `BROWSER_API_TOKEN`, `CHROME_PROFILE_DIR`, and, when necessary,
`CHROME_EXECUTABLE_PATH` before starting. The image defaults to
`/usr/bin/google-chrome-stable`; local checks may point to another installed
Chrome executable.

```bash
npm test --prefix deploy/chrome-worker
CHROME_EXECUTABLE_PATH=/usr/bin/google-chrome npm run test:browser --prefix deploy/chrome-worker
```

The integration test connects the official MCP SDK client, initializes a session,
lists Playwright tools, navigates a synthetic page, snapshots, types, clicks and
screenshots, and checks authentication and session termination. It needs no
external site or account. MCP-generated artifacts stay in the private
`/state/profile/mcp-output` directory by default; no profile/artifact HTTP routes
are exposed.

## ChatGPT OAuth

For ChatGPT, configure `BROWSER_PUBLIC_URL` to the deployed HTTPS origin and use
GitHub OAuth with `BROWSER_GITHUB_CLIENT_ID`, `BROWSER_GITHUB_CLIENT_SECRET`, and the
single permitted GitHub account ID in `BROWSER_GITHUB_ALLOWED_ID` (or
`ALICE_GITHUB_ALLOWED_IDS`). The existing `ALICE_GITHUB_CLIENT_ID` and
`ALICE_GITHUB_CLIENT_SECRET` names remain supported as a fallback. The OAuth
application's callback is:

`<BROWSER_PUBLIC_URL>/browser/oauth/github/callback`

The server publishes MCP protected-resource and OAuth authorization-server
metadata, dynamic client registration, authorization-code flow with PKCE S256,
and rotating refresh tokens. The GitHub sign-in must match the configured
account. ChatGPT connects to `<BROWSER_PUBLIC_URL>/browser/v1/mcp` using OAuth.
`BROWSER_API_TOKEN` remains available only for machine/operations clients.

Use a dedicated GitHub OAuth App if Alice's app has a different callback domain.
Register the exact callback above and put its client ID and secret in the
production GitHub Actions secrets; never put them in the plugin archive or logs.
Having nonempty credentials does not establish that the callback is registered.
The final acceptance step requires the owner's real GitHub sign-in from ChatGPT.

## Cloud.ru runtime state

The Cloud.ru lane uses a separate Chrome container and state bucket; it does not
replace the RDC container. The public endpoint is
`https://<container-name>.maxxxpavlov.online` on API Gateway.
OAuth runs behind Gateway; direct Container Apps control remains
protected by the provider's authentication. The worker and its OAuth metadata use the public
Gateway origin consistently.

Keep `CHROME_PROFILE_DIR=/tmp/chrome-profile` on local storage and mount the
private state volume at `CHROME_STATE_DIR=/chrome-state`. OAuth state lives at
`BROWSER_OAUTH_STATE_FILE=/tmp/chrome-auth/oauth.json` with
`BROWSER_OAUTH_STATE_DIR=/tmp/chrome-auth`. At startup, state is restored before
OAuth or browser requests are accepted. Sleep and SIGTERM close Chrome before
saving its profile; OAuth writes are checkpointed separately while Chrome runs.
Only one replica/writer may own the state volume. Deployments must checkpoint
and stop the previous replica before starting a new revision.

`GET /healthz` exposes readiness and deployment SHA without authentication. It
returns no browser URL, profile data or credentials. Browser status and sleep
remain authenticated operations. Live deployment acceptance includes unauthenticated
MCP rejection, OAuth metadata, an authenticated MCP browser flow, and profile
recovery after container restart.

## Deployment

The `Cloud.ru persistent Chrome MCP` workflow operates only from reviewed
`master`. It uses the production Cloud.ru IAM credentials and Object Storage
tenant ID. Run `preflight` before `deploy`; the default public origin is
`https://chrome-<project-prefix>.maxxxpavlov.online`, using the first 12 project
UUID digits as the prefix. `BROWSER_PUBLIC_URL` can supply the exact origin.

Preflight inventories the existing Evolution DNS zone, a matching enabled
Certificate Manager certificate and the dedicated API Gateway before creating
resources. It stops on conflicting records, absent certificates or missing
credentials. It does not modify certificate contents, the zone apex or other
applications. `scripts/chrome_gateway_spec.py` renders the exact MCP, OAuth,
health and legacy REST routes; no catch-all is added.

Deployment creates a private image registry/repository, a private state bucket
and a single 1 CPU / 4 GiB Chrome replica. It verifies Chrome startup and a real
state checkpoint/restart before publishing the Gateway. Existing deployments
checkpoint and stop the previous replica before replacing it; a worker rollout
failure restores the prior configuration. A later Gateway failure leaves the
verified private worker available for retry and reports a failed deployment.
The public URL is accepted only after TLS, health, OAuth metadata and anonymous
access rejection pass through the Gateway.

The workflow then runs the official MCP SDK through the public Gateway: client
registration and consent redirects, navigation, a synthetic form interaction,
and a screenshot. It writes a temporary cookie/localStorage marker on
`example.com`, restarts the private container, verifies both values through MCP
and removes the marker. This checks transport and profile recovery without
claiming that the owner has completed GitHub or ChatGPT consent.

The `restart` and `stop` workflow actions checkpoint the current profile before
stopping the worker. Status output contains resource IDs and readiness evidence,
not environment secrets or profile contents.
