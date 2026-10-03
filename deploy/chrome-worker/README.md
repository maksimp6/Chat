# Chrome + Playwright MCP

The worker runs official Google Chrome Stable with the persistent profile at
`/state/profile`. Mount the private state volume there. Chrome's sandbox is
explicitly disabled (`chromiumSandbox: false`). The Dockerfile retains the
original Russian CA installation and certificate files.

## MCP connection

`/browser/v1/mcp` runs the official `@playwright/mcp` server over MCP Streamable
HTTP, using the MCP SDK transport. It supports POST (initialize, notifications and
tool calls), GET (event stream), and DELETE (end session). Every request requires
`Authorization: Bearer` with `BROWSER_API_TOKEN`, including requests carrying an
`Mcp-Session-Id`. The Gateway must forward this session header, the MCP protocol
version header and Accept header, and return `Mcp-Session-Id` to the client.

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
