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
requests carrying an `Mcp-Session-Id`.

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

Use the deployed Container Apps origin with path `/browser/v1/mcp` in your client.
For a locally running worker, the Codex configuration is:

```toml
[mcp_servers.playwright]
url = "http://127.0.0.1:8080/browser/v1/mcp"
bearer_token_env_var = "BROWSER_API_TOKEN"
```

Set the token through the client's environment/secret store. In a remote setup,
replace the local URL with the deployed endpoint. No live URL
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

For ChatGPT, configure `BROWSER_PUBLIC_URL` to the deployed HTTPS origin.
The OAuth consent page asks the owner for the existing short token
(`BROWSER_API_TOKEN`, with `ALICE_SHORT_TOKEN` as the deployment fallback).
The short token is compared in memory, is never written into OAuth state, and
never leaves the worker. A successful check completes the authorization-code
flow with PKCE S256 and rotating refresh tokens.

No GitHub OAuth application, callback registration, Alice deployment, or custom
DNS is required for this temporary owner-authentication path. ChatGPT connects
to `<BROWSER_PUBLIC_URL>/browser/v1/mcp` and enters the short token when the
authorization page opens.

## Cloud.ru runtime state

The Cloud.ru lane uses a separate Chrome container and state bucket; it does not
replace the RDC container. The public endpoint is the container's own stable
Container Apps origin (`https://<host>.containerapps.ru`), kept for as long as
the container name `chrome-<project-prefix>` is unchanged. Evolution API Gateway
is not used: it has no public management API, and `apigw.api.cloud.ru` returned
NXDOMAIN from Google and Cloudflare DNS on 2026-10-04.

Provider IAM authentication on the container ingress is disabled because ChatGPT
cannot send a Cloud.ru IAM token. The worker itself is the access boundary:

- Anonymous: `GET /healthz` and the protocol-required OAuth surface (protected
  resource and authorization server metadata, dynamic client registration,
  authorize, GitHub callback, token and revoke). Tokens are issued only after
  GitHub sign-in by the single allowed account.
- Authenticated: browser control (`/browser/v1/*`) and MCP (`/browser/v1/mcp`)
  require `BROWSER_API_TOKEN` or such an OAuth access token.

Every deployment and restart verifies on the public origin, without credentials
or redirects, that `/healthz` serves the exact deployed revision (SHA, state and
OAuth readiness) and that `/browser/v1/status` and `/browser/v1/mcp` answer 401.

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

The `Cloud.ru persistent Chrome MCP` workflow runs from reviewed `master` by manual
dispatch or by an exact owner-only command on issue #409 (`/chrome preflight`,
`/chrome deploy`, `/chrome status`, `/chrome restart`, `/chrome stop`). Runs are
serialized; merging a pull request does not start it.
It uses the production Cloud.ru IAM credentials and Object Storage tenant ID.
Run `preflight` (read-only) before `deploy`.

Deployment creates a private image registry/repository, a private state bucket
and at most one 0.5 vCPU / 1 GiB Chrome replica. Container Apps only offers
fixed CPU/memory pairs; Chrome measured about 340 MiB idle and 800 MiB on a heavy
page, so this is the economical size. The service scales from zero: Cloud.ru
stops the replica after 15 minutes without requests (the profile is saved on
SIGTERM) and starts it on the next request, so the first call after idle waits
for a cold start. The container is found by its fixed
name; an existing one is checkpointed, stopped and updated in place, two
containers with that name stop the run, and a new one is created only when none
exists. On first install the provider assigns the origin, and `BROWSER_PUBLIC_URL`
is then bound to that same container before the restart check, so OAuth metadata
and callbacks use it. A rollout failure restores the prior configuration, or
suspends only the new container on first install.

The workflow then runs the official MCP SDK against the deployed origin: client
registration and consent redirects, navigation, a synthetic form interaction,
and a screenshot. It writes a temporary cookie/localStorage marker on
`example.com`, restarts the container, verifies both values through MCP
and removes the marker. This checks transport and profile recovery without
claiming that the owner has completed GitHub or ChatGPT consent.

After the first deploy, register `<provider_url>/browser/oauth/github/callback`
(from the deploy output) as the GitHub OAuth App callback, then add
`<provider_url>/browser/v1/mcp` in ChatGPT.

The `restart` and `stop` workflow actions checkpoint the current profile before
stopping the worker. Status output contains resource IDs and readiness evidence,
not environment secrets or profile contents.
