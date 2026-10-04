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

## Test lane: passphrase sign-in

`IDP_OWNER_PASSPHRASE` (at least 16 characters) replaces GitHub: the consent page asks for the passphrase and, if it matches, issues the code directly, signing the owner id from `IDP_ALLOWED_GITHUB_IDS` (the first entry). GitHub credentials are then optional. The browser binding, redirect and resource allowlists and PKCE still apply. Five wrong attempts lock sign-in for ten minutes (in memory, so keep `maxInstanceCount` at 1), and `/healthz` reports `sign_in: "passphrase"`.

Production uses GitHub only. The IdP refuses to start (`/healthz` 503, `missing` names the conflict) if `IDP_OWNER_PASSPHRASE` is set together with `IDP_GITHUB_CLIENT_ID` or `IDP_GITHUB_CLIENT_SECRET`, so a production configuration cannot be switched to passphrase sign-in by mistake.

## Deployment (Cloud.ru Container Apps)

`scripts/cloudru_idp.py` and the `Cloud.ru Alice OAuth IdP` workflow (`workflow_dispatch`) deploy a stateless 0.1 vCPU / 256 MiB container that scales to zero. Actions: `preflight`, `deploy`, `status`, `stop`.

| Lane | Source | Sign-in | Secrets | Trusts |
| --- | --- | --- | --- | --- |
| `production` | reviewed `master` only | GitHub | `IDP_SECRET`, `IDP_GITHUB_CLIENT_ID`, `IDP_GITHUB_CLIENT_SECRET` | the production Chrome worker |
| `test` | any branch | passphrase | `IDP_TEST_SECRET`, `IDP_TEST_PASSPHRASE` | the test Chrome worker |

Each lane gets only its own credentials and never shares signing keys, so a test token cannot open the production browser. After deploy, set `BROWSER_IDP_ISSUER` for the Chrome lane (the `idp_issuer` input of the Chrome workflow, or the variables `BROWSER_IDP_ISSUER` / `BROWSER_IDP_ISSUER_TEST`) and deploy the worker again.

The deploy verifies on the public origin that `/healthz` reports the exact commit and the lane's sign-in mode, that the metadata advertises the configured issuer, and that exactly one Ed25519 signing key is published. A failed update restores the previous revision.

### Custom domain `oauth.maxxxpavlov.online` (production)

Container Apps cannot serve a custom domain itself, and API Gateway has no public management API, so these steps are done once in the Cloud.ru console:

1. Create a Shared API Gateway (Development -> API Gateway) that proxies to the IdP's `*.containerapps.ru` origin (the `provider_url` from the deploy summary).
2. Attach `oauth.maxxxpavlov.online` with a Certificate Manager certificate.
3. Add the CNAME `oauth.maxxxpavlov.online -> <gateway-uuid>.apigw.cloud.ru.` in Evolution DNS (`deploy/cloudru/dns/README.md`).
4. Deploy production with the `public_url` input (or the variable `IDP_PUBLIC_URL`) set to `https://oauth.maxxxpavlov.online`, create the GitHub OAuth App with callback `https://oauth.maxxxpavlov.online/github/callback`, and set `BROWSER_IDP_ISSUER` to the same origin.

The issuer is part of every token, so changing it later means reconnecting ChatGPT once and updating the GitHub callback.

