# Alice Pro production deployment

Production uses the existing VPS reverse proxy and Docker network already used by Preview.

## Domains and DNS

Point both domains to the Alice Pro VM public address:

- `maxxxpavlov.ru`
- `maxxxpavlov.online`

Use the required A/AAAA records for the VM. Do not put the production token into DNS, Git, Docker labels, or application configuration files.

The production workflow is manually triggered, but certificate issuance itself is automatic. Before the first production deployment, DNS must resolve both domains to the VM and inbound TCP ports 80 and 443 must be reachable from the Internet.

## TLS certificate

The shared Traefik instance uses Let's Encrypt ACME with the HTTP-01 challenge. Traefik listens on port 80 for validation and port 443 for HTTPS. Each production HTTPS router references the `letsencrypt` resolver and requests a certificate covering both production domains. This is the standard Traefik ACME configuration.

ACME state is kept only on the VM at:

`/opt/alice-preview/keys/letsencrypt/acme.json`

The deployment creates the file if necessary and enforces mode `600`. The directory is mounted into Traefik as `/letsencrypt`; neither certificates nor ACME account state are committed to GitHub.

No email activation code is required. The configured ACME email is `Maxxxxpavlov@yandex.ru` and is used for the Let's Encrypt account/notifications.

Traefik renews certificates automatically using the persisted ACME state.

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
4. upgrades the shared Traefik container to HTTP + HTTPS, file-provider support, and persistent ACME storage;
5. passes `ALICE_SHORT_TOKEN` over SSH stdin;
6. builds and starts the production container;
7. checks both public `/healthz` endpoints over HTTPS;
8. inspects the live TLS certificates for both domains;
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

## Rotation

To rotate the token, generate a new high-entropy URL-safe value and replace `ALICE_SHORT_TOKEN` in the `production` environment. The next production deployment starts the application with the new secret; existing session cookies signed with the previous token become invalid.

Do not place the old or new value into Issues, pull requests, logs, screenshots, Execution Trace, or documentation.

## Rollback

Run the production workflow again with the previous known-good Git ref after restoring the intended secret and certificate state on the VM.

For an immediate service stop, remove the `alice-production` container on the VPS. Preview containers and the shared Traefik entry point remain separate resources.

## Backups and restore

Production data lives in Cloud.ru Managed PostgreSQL once `ALICE_DATABASE_URL` points at it. Two layers protect it:

1. **Managed backups.** In the Cloud.ru console, enable scheduled automatic backups for the cluster and keep point-in-time recovery on. This is the first choice for disaster recovery: restore the cluster (or a new cluster) to a moment before the incident, then point `ALICE_DATABASE_URL` at it.
2. **Logical dumps.** `scripts/pg_backup.sh` makes a portable `pg_dump` archive that can be restored into any PostgreSQL, including a local one. Use it before risky migrations and for off-cluster copies.

```bash
export ALICE_DATABASE_URL=postgresql://...        # never paste the value into issues or logs
scripts/pg_backup.sh backup alice-$(date +%F).dump
scripts/pg_backup.sh verify alice-$(date +%F).dump
```

`verify` restores the dump into a scratch database, compares exact row counts of every user table with the data inside the dump (so writes to production after the dump do not matter), and drops the scratch database. Managed PostgreSQL users often cannot `CREATE DATABASE`; in that case create an empty database in the console and pass it as `ALICE_RESTORE_CHECK_URL`. Client tools must be at least the server's major version; set `PG_DUMP`, `PG_RESTORE` and `PSQL` to a matching client (the CI job uses the `postgres:17-alpine` image).

CI runs `backup` and `verify` against the full application schema on every pull request (job **PostgreSQL integration**), including a write after the dump, so a schema change that breaks restore fails before merge.

### Restore runbook

1. Stop writes: scale the Container App to zero instances or stop the production container.
2. Pick the source: a managed backup / point-in-time moment (preferred), or a dump file.
3. Managed backup: restore into a **new** cluster from the console, run `scripts/pg_backup.sh verify` against a fresh dump of it if time allows, then switch `ALICE_DATABASE_URL` to the new cluster.
4. Dump file: create an empty database, then run `ALICE_RESTORE_TARGET_URL=postgresql://... scripts/pg_backup.sh restore <dump>`. `restore` only writes to `ALICE_RESTORE_TARGET_URL` and never defaults to production.
5. Start the app and check `/healthz`, sign-in, and the latest conversations.
6. Keep the old cluster until the restored one has run for a day.

Changing production data or `ALICE_DATABASE_URL` needs the owner's approval (see `AGENTS.md`).

## Cloud budget limits

Alice reads real spend from the Cloud.ru billing API (`organization.api.cloud.ru`, method `consumption`) and compares it with a monthly limit.

| Variable | Meaning |
| --- | --- |
| `CLOUDRU_BILLING_ENDPOINT` | `https://organization.api.cloud.ru` |
| `CLOUDRU_BILLING_SUMMARY_PATH` | consumption method path, may include query parameters such as `customer_id` or dates |
| `CLOUDRU_MONTHLY_BUDGET` | monthly limit; unset disables the guard. Production sets `10000` (RUB) in `deploy/production/server.sh` |
| `CLOUDRU_BUDGET_WARN_RATIO` | warning threshold as a share of the limit, default `0.80` |
| `CLOUDRU_BILLING_CURRENCY` | limit currency, default `RUB` |

The service account needs the `platform.customer.expense-admin` role (or organization admin) to read consumption.

Behaviour:

- `cloud.budget.status` shows spend, limit, remaining amount and a status: `ok`, `warn`, `block`, `unconfigured` or `unknown`.
- At `block`, cost-creating operations (`cloud.compute.start`) are refused with `budget_exceeded`. Stopping resources and backups are never blocked.
- If billing is unreachable, returns an unusable total, or reports a different currency, the status is `unknown` and nothing is blocked.
- The guard reads billing for the current month (`YYYY-MM`) at most once every 5 minutes per process; `cloud.budget.status` with `refresh: true` reads it immediately.
- Warn, block and unknown results are recorded in the ExecutionTrace as `cloud_budget_<status>` events.

For a hard stop that does not depend on Alice, also set a budget alert in the Cloud.ru console.

## Troubleshooting

If certificate issuance fails, verify that both DNS names resolve to the VM, TCP ports 80/443 are reachable, and `/opt/alice-preview/keys/letsencrypt/acme.json` exists with mode `600`. The Traefik container logs contain the ACME validation error without requiring the account key to be copied elsewhere.

If `/healthz` succeeds but `/` returns anything other than `401` before authentication, inspect the production container environment and ensure `ALICE_REQUIRE_SHORT_TOKEN=1` is present.

If Preview stops routing, inspect the shared `alice-preview-traefik` container and rerun the Preview deployment workflow so its HTTP/HTTPS and file-provider configuration is recreated.

Never diagnose authentication by printing the value of `ALICE_SHORT_TOKEN`.
