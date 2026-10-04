# Alice identity provider

One OAuth 2.1 authorization server for every Alice service. The owner signs in
with GitHub **here, once**; services such as the Chrome MCP worker only verify
the access tokens it issues. GitHub is configured with a single callback URL,
`<IDP_PUBLIC_URL>/github/callback`, for all services, now and later.

```text
ChatGPT / Claude ──register, authorize──▶ IdP ──GitHub sign-in (one callback)──▶ GitHub
        │                                  │
        └────── Bearer access token ───────┴──▶ service (verifies signature, iss, aud, scope)
```

## Design

- **Stateless.** Dynamic client registrations, authorization transactions,
  codes and refresh tokens are AES-256-GCM sealed values (`crypto.mjs`); access
  tokens are EdDSA JWTs. Everything is derived from `IDP_SECRET`. There is no
  database, no volume and no checkpoint, so the service scales to zero, survives
  restarts, and an anonymous registration flood has nothing to fill up.
- **Fail closed.** Missing configuration makes `/healthz` return 503 with the
  names (never values) of the missing settings, and every other route 503.
- **Owner only.** Sign-in succeeds only for GitHub numeric IDs in
  `IDP_ALLOWED_GITHUB_IDS`, checked again on every token issue and refresh.
- **Pre-approved services.** Tokens are issued only for `resource` values in
  `IDP_ALLOWED_RESOURCES` (RFC 8707) and carry it as `aud`.
- **Allowed return addresses.** Registration accepts only https addresses on
  `IDP_ALLOWED_REDIRECT_HOSTS` (default `chatgpt.com`, `chat.openai.com`,
  `claude.ai`, `claude.com`) and `http` loopback addresses for local clients.
- PKCE S256 is required. The consent page is cookie-bound to the browser that
  started it, escapes everything, sends `Referrer-Policy: no-referrer`, and its
  CSP allows form submission only to itself and `github.com`.

## Configuration

| Variable | Meaning |
| --- | --- |
| `IDP_SECRET` | Random secret, 32+ characters (`openssl rand -base64 48`). Rotating it revokes every token and client. |
| `IDP_PUBLIC_URL` | Public https origin, no path, e.g. `https://oauth.maxxxpavlov.online`. Changing it changes the issuer. |
| `IDP_GITHUB_CLIENT_ID`, `IDP_GITHUB_CLIENT_SECRET` | A GitHub OAuth App used only by this service, with callback `<IDP_PUBLIC_URL>/github/callback`. |
| `IDP_ALLOWED_GITHUB_IDS` | Comma-separated numeric GitHub user IDs allowed to sign in. |
| `IDP_ALLOWED_RESOURCES` | Comma-separated service URLs tokens may be issued for. |
| `IDP_SCOPES` | Comma-separated scopes (default `mcp`; the Chrome worker uses `browser`). |
| `IDP_ALLOWED_REDIRECT_HOSTS` | Optional override of the return-address host allowlist. |
| `IDP_NOT_BEFORE` | Optional Unix time; refresh tokens issued earlier are refused. |

## Operations

- **Add a service:** add its resource URL to `IDP_ALLOWED_RESOURCES` and redeploy;
  the service verifies tokens with `jwt-verify.mjs` against `<issuer>/jwks.json`.
- **Revoke:** remove the user or resource from the allowlists, raise
  `IDP_NOT_BEFORE`, or rotate `IDP_SECRET`. Access tokens live one hour.
- **Lifetimes:** code 60 s, access token 1 h, refresh token 30 days (sliding),
  sign-in transaction 10 min, client registration 2 years.

## Known limits

- An authorization code is single-use per running instance; replay protection is
  in memory, so keep `maxInstanceCount` at 1. Codes also last only 60 s and need
  the PKCE verifier.
- Refresh tokens rotate but reuse is not detected (that would need storage).
  Revocation is by configuration, as above.
- `/token` and `/register` send no CORS headers: browser-only clients are out of scope.

## Tests

`npm test --prefix deploy/oauth-idp` runs the crypto, JWT and full-flow tests
against a real HTTP server with a fake GitHub and an injected clock.

## Test lane: auto-approve

`IDP_AUTO_APPROVE=1` skips GitHub: the consent button issues the code directly, signing the owner id from `IDP_ALLOWED_GITHUB_IDS` (the first entry). GitHub credentials are then optional. The browser binding, redirect and resource allowlists and PKCE still apply, and `/healthz` reports `auto_approve: true`. Use it only for the test lane, whose worker has a fresh profile with no logins; never in production.
