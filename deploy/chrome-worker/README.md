# Persistent Chrome Worker

This image runs official Google Chrome Stable through `playwright-core`. Its
persistent user-data directory is `/state/profile`; production must mount the
existing private state volume there. `POST /browser/v1/sleep` closes Chrome so
the profile is flushed before the container scales down. Any authorized
`POST /browser/v1/wake` starts Chrome again with that same directory.

Every route, including status, requires `Authorization: Bearer` with the value
from `BROWSER_API_TOKEN`. Set `BROWSER_ALLOWED_HOSTS` to a comma-separated exact
hostname allowlist. The worker must remain private; API Gateway is the supported
ingress. `/browser/v1/mcp` provides the same bounded operations as MCP tools over
JSON-RPC, without exposing CDP, cookies, arbitrary JavaScript, or shell access.

The image uses the distribution TLS trust store without installing additional
Russian roots. The archived certificate files are not copied into the image.
TLS validation remains enabled.

An empty `BROWSER_ALLOWED_HOSTS` prevents wake. Only exact DNS hostnames are
accepted (no wildcards or IP literals). At wake, public IPv4 answers are checked
and pinned in Chrome's resolver for the browser lifetime; private/reserved answers
are rejected and all other DNS names fail closed. IPv6-only sites are unsupported.
Context request interception applies the allowlist to page navigation, redirects,
popups and subresources. Service workers and WebSockets are blocked. All required
asset/login hosts must be explicitly listed. Sleep/wake refreshes DNS pins.
Deploy with an egress firewall denying private, loopback, link-local and metadata
networks as defense in depth; browser flags are not a replacement for network
isolation. The persistent profile must not contain unreviewed extensions.

Live acceptance remains required before merge/cutover: build the pinned image,
start sandboxed Chrome in Cloud.ru, verify authenticated Gateway navigation,
extract and screenshot, reject anonymous/direct worker access, persist a synthetic
cookie across sleep plus container recreation using the same state volume, and
verify Alice ExecutionTrace correlation and extension restoration. Fake-browser
unit tests do not establish those deployment outcomes. See issue #751 and PR #752.

```bash
npm ci --prefix deploy/chrome-worker
npm test --prefix deploy/chrome-worker
CHROME_EXECUTABLE_PATH=/usr/bin/google-chrome npm run test:browser --prefix deploy/chrome-worker
```

Chrome runs with `chromiumSandbox: true`. The runtime must permit Chrome's
namespace sandbox. A container that rejects namespace creation with
`Operation not permitted` is not an accepted runtime; do not work around that
failure with `--no-sandbox`. The browser regression suite uses synthetic pages
and cookies and requires no external site or account.
