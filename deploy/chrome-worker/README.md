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

The image installs reviewed copies of the Russian Trusted Root CA and its
subordinate CA published by the Ministry of Digital Development at `gu-st.ru`.
Their SHA-256 values are recorded in `certificates/SHA256SUMS`; update them only
after verifying a new official publication. They extend TLS trust only and do
not add GOST client-signing or CryptoPro support.

```bash
npm ci --prefix deploy/chrome-worker
npm test --prefix deploy/chrome-worker
```
