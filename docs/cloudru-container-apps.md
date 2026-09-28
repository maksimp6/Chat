# Cloud.ru Container Apps deployment (baseline)

Status: baseline merged; IAM and the proposed v2 inventory fix verified read-only;
production rollout pending. API Gateway and persistent bucket volumes are documented,
but SQLite on a bucket remains an unverified pilot option (2026-09-28 UTC).
Issue: #427

Alice Pro runs as one Cloud.ru Evolution **Container Apps** service built from an
exact commit and pulled from **Artifact Registry** by digest. No VM or Kubernetes
cluster is created. Evolution has no separate Functions product for Docker
images, so Container Apps is the serverless runtime.

## Pieces

| File                                     | Role                                                                                                                                  |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `cloud/cloudru/registry_client.py`       | Registry list/create/delete (`ar.api.cloud.ru`), `docker login`/build/push, digest pinning                                            |
| `cloud/cloudru/container_apps_client.py` | Container service create/update/delete/start/stop/status (`containers.api.cloud.ru`), readiness wait, `/healthz` check, cost estimate |
| `scripts/cloudru_deploy.py`              | CLI: `deploy`, `status`, `delete --yes`, `estimate` (JSON output, no secret values)                                                   |
| `.github/workflows/cloudru-deploy.yml`   | `preflight`, `estimate`, `status` and complete deployment through `workflow_dispatch` in the existing `production` environment |

Both clients reuse `CloudRuClient`, so requests are traced the same way as the
rest of the Cloud.ru provider. They authenticate with an IAM bearer token from
the service-account key pair and ignore `CLOUDRU_API_KEY` (Foundation Models).

## Persistence: current deployment versus the bucket-volume candidate

The current [Cloud.ru Container Apps FAQ](https://cloud.ru/docs/container-apps-evolution/ug/topics/faq__database-connection),
checked on 2026-09-28, explicitly says that Container Apps cannot connect to
Cloud.ru Managed PostgreSQL. The earlier plan to provision those two services
and pass a connection string between them is therefore not a supported deployment
architecture. A PostgreSQL URL passing the CLI's syntax check does not establish
network connectivity from the application container.

The current CLI and GitHub deployment workflow require `ALICE_DATABASE_URL` to
select PostgreSQL. This is a guard in Alice's deployment implementation, not a
claim that Container Apps has no persistent filesystem option. The official
[volume documentation](https://cloud.ru/docs/container-apps-evolution/ug/topics/concepts__volumes)
supports mounting an Object Storage bucket from the same project and explicitly
includes a SQLite file among its examples. However, the provider's
[Django tutorial](https://cloud.ru/docs/container-apps-evolution/ug/topics/tutorials__deploy-django-photo-app)
labels this SQLite configuration as a demonstration and recommends PostgreSQL
for production because of concurrent writes. A mount alone is not evidence that
Alice's database workload is safe there.

For the existing PostgreSQL path, evaluate an external database reachable from
Container Apps, including network/TLS/access controls. The FAQ describes access
over the internet and warns about exposure. In parallel, the owner's proposed
bucket-volume architecture can be evaluated on synthetic data under the pilot
criteria below. Neither a new VM nor Kubernetes is an implicit fallback; both
remain outside the agreed scope of #427.

Test the selected connection from the actual application runtime, including TLS,
authentication, and persistence across an instance/revision restart. A connection
from a Codex or GitHub runner alone does not prove that Container Apps can reach it.
Keep the existing production data until its backup, transfer and verification
procedure has been reviewed.

## Proposed API Gateway and Object Storage pilot

The candidate is API Gateway -> one Container Apps service -> Foundation Models,
with private Object Storage for files and an experimental persistent SQLite
volume. This can avoid a separately managed database service; it is not a design
without a database. Alice's `db.py` persists conversations, messages, settings,
provider credentials and key-management data independently of gateway access.

According to the current
[API Gateway release notes](https://cloud.ru/docs/api-gateway-svp/ug/topics/overview__release-notes),
Evolution API Gateway is free during Public Preview and supports Container Apps
backends plus IAM/API-key authentication. Its former internal username/password
authorization was removed. The
[policy guide](https://cloud.ru/docs/api-gateway-svp/ug/topics/guides__configure-policy)
also documents OpenID Connect; this is not confirmation of arbitrary custom-JWT
validation without an identity provider. Its remaining basic-auth instructions
conflict with the newer release notes and should not be used.

Gateway configuration must preserve Alice's existing owner login and MCP/OAuth
flows until their replacement is designed and tested. IAM identities, service
API keys and Alice users are different things. Never distribute the deployment
service account's IAM key as an application login or embed a shared secret in
browser code. Gateway caller authorization and authorization to the Container
Apps backend must be configured and tested separately.

For the pilot, keep response caching disabled for chat, authenticated data,
OAuth and MCP/streaming routes. Check rate-limit responses (use 429), cold-start
and streaming timeouts, cookies/redirects, MCP discovery and token exchange.
Prove that the backend cannot bypass the intended access policy. API Gateway
has a management API; Preview alone is not a reason to require manual console
configuration.

### Persistence acceptance criteria

1. Use a separate private bucket and synthetic data. Pin the image digest and
   record the tested revision. Mount a dedicated empty directory, for example
   `/data/alice`, and verify read/write access as the image's UID/GID 10001.
   The eventual database path would be `/data/alice/alice_pro.db`; setting
   `ALICE_DB_PATH` alone does not create a persistent mount.
2. Establish the provider's locking, flush/durability and revision-overlap
   semantics. `db.py` currently forces `journal_mode=WAL` and
   `synchronous=NORMAL`; [SQLite's WAL documentation](https://sqlite.org/wal.html)
   excludes shared access across network hosts. Do not assume the bucket mount
   meets WAL's requirements. A switch to a rollback journal also needs filesystem
   guarantees and is not a generic fix for an object-store mount.
3. Enforce a single active database writer across revisions as well as a maximum
   of one instance. `maxInstanceCount=1` by itself is not proof that an old and
   new revision cannot overlap. Requests/background threads inside one instance
   can also write concurrently. Leave scale-to-zero disabled for initial tests;
   exercise it explicitly before enabling it in the proposed 0-to-1 setup.
4. Verify committed chats/settings after process termination, container stop/start,
   idle scale-to-zero, a new revision and rollback. Check `PRAGMA integrity_check`,
   concurrent transactions, backup restoration and write failures such as a full
   bucket. Record committed transaction IDs outside the container so a fresh empty
   database cannot accidentally pass the persistence test.
5. Preserve a consistent SQLite backup and any required provider-credential
   encryption key before considering real data. Do not copy an open SQLite file
   without its transaction state. Local tests and one successful restart do not
   establish the mount's production durability guarantees.

Only after these criteria and the provider's demo-only guidance are resolved
should a focused change allow persistent SQLite through production preflight.
The existing PostgreSQL guard stays in place meanwhile. No volume-enabled Alice
deployment or gateway has been created or verified by these documentation checks.

## Verified readiness (2026-09-28)

The [read-only runner report in #427](https://github.com/maksimp6/Chat/issues/427#issuecomment-5879401586)
verified the exact merged baseline `a0f1f5277f7d612fd3f44622b48342a21aadfb2d`:

- The existing protected Codex hand-off loads the IAM key pair and project ID;
  IAM token exchange succeeds.
- Artifact Registry inventory succeeds and returns resources. This does not yet
  establish that a suitable private Docker registry has been selected.
- The client's `GET /v1/containers/alice-pro` returns HTTP 499. The current
  [official OpenAPI](https://cloud.ru/docs/api/specs/container-apps-evolution/ug/_specs/openapi.yaml)
  specifies `GET /v2/containers` for inventory; that request succeeds with HTTP
  200 and an empty `data` list. There are no Container Apps in the selected project.
  [Follow-up diagnostics](https://github.com/maksimp6/Chat/issues/427#issuecomment-5879541960)
  also observed HTTP 499 for the missing name on v2. A path change alone is not
  enough: confirm absence through complete successful inventory, not a blanket
  interpretation of 499 as "not found".
- The [published v2 fix's live smoke](https://github.com/maksimp6/Chat/pull/474#issuecomment-5879672549)
  verified `b10dfeb`: status exits successfully with `NOT_FOUND` after the 499
  detail response and successful empty v2 inventory. This verifies reads, not
  create/update operations or a live application rollout.
- Managed PostgreSQL `GET /v1/clusters` succeeds with HTTP 200 and an empty
  `clusters` list. No existing cluster or authorized `ALICE_DATABASE_URL` source
  was found in the runner.
- IAM service-account listing returns HTTP 415 with `application/grpc`, both with
  and without a request `Content-Type`. Removing that header does not fix the
  response; it is not evidence of invalid credentials.
- Object Storage inventory remains unverified because the runner lacks the S3
  access-key pair and tenant configuration. IAM bearer authentication is not a
  substitute for S3 SigV4 authentication.
- Database connectivity, image rollout, HTTPS/auth smoke checks, and persistence
  after restart remain unverified.

No cloud deployment has been verified. The successful GitHub `estimate` run is
an offline calculation. GitHub `preflight` currently reports missing
`CLOUDRU_IAM_KEY_ID`, `CLOUDRU_IAM_KEY_SECRET` and `ALICE_DATABASE_URL`; credentials
available in Codex are not automatically available to GitHub Actions. Diagnose
through the runner that already has authorized access rather than copying keys
between secret stores as part of an inventory check.

## One-time setup

The steps below describe the implemented PostgreSQL deployment path. Resolve
persistence and review the selected resources/cost before running `action: deploy`.
The bucket-volume pilot does not yet have a production deployment path.

1. Create a service account in the target project with Artifact Registry
   (push) and Container Apps (admin) roles, and issue an access key.
2. In GitHub, use the existing `production` environment (so the configured short
   token is reused) with:
   - secrets `CLOUDRU_IAM_KEY_ID`, `CLOUDRU_IAM_KEY_SECRET`, `ALICE_SHORT_TOKEN`,
     `ALICE_DATABASE_URL`, `ALICE_GITHUB_CLIENT_ID`, `ALICE_GITHUB_CLIENT_SECRET`,
     and optionally `ALICE_PROVIDER_CREDENTIAL_KEY`; repository-level OAuth
     secrets are inherited, so do not duplicate them;
   - variable `CLOUDRU_PROJECT_ID`, and optionally `CLOUDRU_REGISTRY_NAME`,
     `CLOUDRU_REPOSITORY_NAME`, `CLOUDRU_CONTAINER_NAME`, `CLOUDRU_CONTAINER_CPU`,
     `CLOUDRU_MIN_INSTANCES`, `CLOUDRU_MAX_INSTANCES`, `ALICE_GITHUB_ALLOWED_IDS`
     (defaults to the owner's immutable GitHub ID `293531601`).

For Codex Cloud, keep the existing `CLOUDRU_KEY_ID` as an environment variable and `CLOUDRU_KEY_SECRET` as a secret in the `Chat` environment. The setup and maintenance scripts normalize those names into a mode-600 cache outside the checkout; `scripts/cloudru_deploy.py` reads that cache after Codex removes setup secrets from the agent phase. Set `CLOUDRU_PROJECT_ID` as an environment variable. Never commit the cache or put `ALICE_DATABASE_URL` in an issue, log, or repository file.

3. Run **Cloud.ru Container Apps deployment** with `action: preflight`. It
   reports all missing configuration names without printing values, installing
   dependencies, or contacting Cloud.ru. Then run `action: estimate`, verify the
   live project tariff and planned resources, and run `action: deploy`.

The deploy action performs the build, push, rollout and health check automatically.
It requires a reachable durable PostgreSQL database and forwards the existing GitHub-login configuration.
The application image installs `requirements-postgres.txt` as well as the main
requirements. A successful preflight checks configuration only; it does not
prove connectivity, IAM permissions, database readiness or a live deployment.

`deploy` creates the registry if missing, pushes `<registry>.cr.cloud.ru/alice-pro:<sha>`,
creates the service (or rolls out a new revision), waits until it runs the new
digest with a public URL, and requires `GET /healthz` to return 200.
If the new revision fails readiness or the health check, it restores the
previous revision's full configuration (image, env, scaling, resources) and
fails the run. Every action runs only on commits that are already on `master`,
checked before dependencies are installed, and secrets are scoped to the steps
that validate configuration or call Cloud.ru (`estimate` gets none). The deploy script builds from a
`git archive` export of the `--tag` commit, so untracked and ignored files never
reach the image. It refuses to deploy without the short-token gate, keeps the
registry credential in a throwaway Docker config, and fails closed if the
registry reports no image digest.

Direct CLI deployment also fetches canonical `master` from `origin` and rejects
any commit outside that history before exporting it. A same-named registry is
reused only when its metadata explicitly says private and `DOCKER`. Control-plane
credential names are rejected by `--env` before provider calls or image builds.
The registry host is fixed to the official `cr.cloud.ru` domain; any
`CLOUDRU_REGISTRY_DOMAIN` override outside that exact allowlist fails before
`docker login`, so the IAM secret cannot be sent to an arbitrary host. The
Container Apps image runs as the unprivileged `alice` user (UID/GID 10001) and
only pre-creates its application data and log directories as writable paths.

## Cost

Defaults: 0.5 vCPU / 1 GiB, scale to zero (`CLOUDRU_MIN_INSTANCES=0`), at most
one instance. With no warm instance there is no fixed floor: you pay only for
the time an instance is awake, at the Container Services pay-as-you-go example
prices (1.8905 RUB per vCPU·h, 1.257 RUB per GB·h) after the monthly free tier
(25 vCPU·h, 50 GB·h). That free tier covers roughly 50 awake hours a month at
this size. Keeping one instance warm (`CLOUDRU_MIN_INSTANCES=1`) costs about
**1,500 RUB/month**. `scripts/cloudru_deploy.py estimate` prints the floor for
the configured size. Check the live tariff before provisioning:
https://cloud.ru/docs/container-apps-evolution/ug/topics/pricing__container-services

The [published tariff effective 2026-09-28](https://cloud.ru/documents/tariffs/evolution/container-apps)
lists 1.891 RUB per vCPU-hour and 1.256966 RUB per GiB-hour including VAT where
applicable. At 730 running hours, 0.5 vCPU / 1 GiB is approximately 1,498 RUB with
the full monthly free allowance available, or 1,608 RUB without it. These are
container-compute estimates, not the project's total bill: database, registry,
storage and other services must be priced separately. The CLI still uses the
documentation's example rates, not a live billing API. A zero cost floor for
`min_instances=0` does not mean that active instances are free.

The first request after idle pays a cold start.

API Gateway is currently free in Public Preview; that is not a permanent pricing
commitment. The current
[Object Storage free tier](https://cloud.ru/docs/evolution/overview/topics/free-tier__object-storage)
covers 15 GB in Standard storage, 100,000 LIST/POST/PUT and 1,000,000 GET/HEAD
operations monthly, plus the first 10 TB of monthly outbound traffic. Excess
usage is billed. The
[Container Services free tier](https://cloud.ru/docs/evolution/overview/topics/free-tier__container-apps)
is 25 vCPU-hours and 50 GB-hours per month, shared with any other qualifying
usage; at the proposed 0.5 vCPU / 1 GiB size, this covers about 50 running hours
if the allowance is unused. Foundation Models, registry and usage above free
allowances still need their own estimate. A mounted database can generate storage
operations even while its file size remains below 15 GB.

## Known limitations

- **The current deployment code requires Postgres.** Unmounted SQLite lives in
  ephemeral container storage. Persistent bucket volumes exist, but the SQLite
  pilot above is not yet production-qualified for Alice.
  Store `ALICE_DATABASE_URL` for the selected reachable PostgreSQL service as a
  secret; the deploy requires and passes it through and the existing Postgres
  backend takes over. Cloud.ru Managed PostgreSQL is not directly supported by
  Container Apps according to the FAQ linked above.
- **In-process state is lost on sleep.** Branch environment runtimes (git
  worktrees under `.alice-environments`), voice sessions, and uploaded files
  live in the container. Keep `CLOUDRU_MAX_INSTANCES=1` until they move to
  Postgres/Object Storage, or requests may land on an instance without them.
- **Scale to zero stops background work.** Background threads and SSH runtime
  containers stop when the instance sleeps; they need to move to request-driven
  work or Container Apps jobs.
- **App secrets are plain container env vars**, visible to anyone with Container
  Apps read access in the project. Move them to a secret store as a follow-up.
- **The baseline API contract needs correction.** [PR #474](https://github.com/maksimp6/Chat/pull/474)
  aligns reads with v2, confirms missing names through paginated inventory after
  HTTP 499, and projects update/rollback bodies onto the official PATCH schema.
  Merge and verify that correction before deployment. Live create/update and
  revision behavior still need verification during the authorized first rollout.
- **Readiness does not track revisions yet.** The deploy waits for the new image
  digest and a running status. A rollout that keeps the same digest (config
  only) can pass that check while the old revision still serves, so the health
  check may hit it. Pin the check to the rollout's revision once the first live
  deploy confirms which field carries it.
