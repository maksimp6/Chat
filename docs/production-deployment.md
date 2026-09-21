# Alice Pro production deployment

Production uses the existing VPS reverse proxy and Docker network already used by Preview.

## Domains and DNS

Point both domains to the Alice Pro VM public address:

- `maxxxpavlov.ru`
- `maxxxpavlov.online`

Use the required A/AAAA records for the VM. Do not put the production token into DNS, Git, Docker labels, or application configuration files.

The production workflow is intentionally manual. Run it only after DNS resolves to the VM and the TLS certificate has been provisioned on the server.

## TLS certificate

Provision the certificate separately on the VM. The deployment expects:

- `fullchain.pem`
- `privkey.pem`

Place them in the directory configured by the repository variable `ALICE_TLS_CERT_DIR`. When that variable is empty, the server-side default is:

`$HOME/alice-preview/certs`

The private key must be readable only by the deployment/runtime account. The files are mounted read-only into the shared Traefik container.

The shared Traefik instance exposes port 443 and uses file-provider TLS configuration. No certificate bytes are committed to GitHub.

## Authentication

Production authentication is enabled with:

`ALICE_REQUIRE_SHORT_TOKEN=1`

The actual token is supplied through the production environment secret:

`ALICE_SHORT_TOKEN`

The workflow sends the secret to the VPS over SSH stdin; it is not placed in the SSH command line, Git, Traefik labels, or a repository file.

The public authentication flow is a token-prefixed path with no redirect:

`https://maxxxpavlov.ru/<short-token>`

or:

`https://maxxxpavlov.online/<short-token>`

A valid token-prefixed request is served directly and creates an HttpOnly session cookie. The token prefix is removed internally before application routing. Sessions expire according to `ALICE_SHORT_TOKEN_TTL_SECONDS` (default: 12 hours).

`GET /healthz` remains public so deployment checks can verify liveness without authenticating.

Preview deployments also require the same `ALICE_SHORT_TOKEN`. Their public path is token-prefixed, for example:

`https://<preview-host>/<short-token>/preview/pr-123/`

Traefik removes the token and preview base path before the application routes the request. The application authenticates the original URI and does not redirect. Direct `/preview/pr-123/` requests are no longer routed.

## Preview isolation

Production routers match the two production Host values but explicitly exclude `/preview/`. Existing Preview routers remain path-based and continue to use the same shared Traefik instance.

This means a request to a production domain such as:

`https://maxxxpavlov.ru/preview/pr-123/`

is not consumed by the production Host router.

## Production workflow

Run **Actions → Production deployment → Run workflow** and select the Git ref to deploy.

The workflow:

1. validates the deployment secrets without printing them;
2. checks out the selected ref;
3. uploads the production deployment script and source archive;
4. upgrades the shared Traefik container to HTTP + HTTPS with file-provider support;
5. validates the server-side certificate files;
6. passes `ALICE_SHORT_TOKEN` over SSH stdin;
7. builds and starts the production container;
8. checks both public `/healthz` endpoints;
9. verifies that `/` returns `401` without authentication.

The workflow does not print the token, write it to the repository, or include it in Traefik labels. GitHub comments intentionally omit the tokenized URL because the URL itself is a bearer credential.

## Required GitHub configuration

Environment: `production`

Secrets:

- `ALICE_SHORT_TOKEN`
- `PREVIEW_SSH_HOST`
- `PREVIEW_SSH_USER`
- `PREVIEW_SSH_PRIVATE_KEY`
- `PREVIEW_SSH_KNOWN_HOSTS`

Optional variables:

- `PREVIEW_SSH_PORT` (default `22`)
- `ALICE_TLS_CERT_DIR` (absolute path on the VPS)

## Rotation

To rotate the token, generate a new high-entropy URL-safe value and replace `ALICE_SHORT_TOKEN` in the `production` environment. The next production deployment starts the application with the new secret; existing session cookies signed with the previous token become invalid.

Do not place the old or new value into Issues, pull requests, logs, screenshots, Execution Trace, or documentation.

## Rollback

Run the production workflow again with the previous known-good Git ref after restoring the intended secret and certificate state on the VM.

For an immediate service stop, remove the `alice-production` container on the VPS. Preview containers and the shared Traefik entry point remain separate resources.

## Troubleshooting

If the workflow reports a missing certificate, verify `fullchain.pem` and `privkey.pem` under `ALICE_TLS_CERT_DIR` on the VM.

If `/healthz` succeeds but `/` returns anything other than `401` before authentication, inspect the production container environment and ensure `ALICE_REQUIRE_SHORT_TOKEN=1` is present.

If Preview stops routing, inspect the shared `alice-preview-traefik` container and rerun the Preview deployment workflow so its HTTP/HTTPS and file-provider configuration is recreated.

Never diagnose authentication by printing the value of `ALICE_SHORT_TOKEN`.
