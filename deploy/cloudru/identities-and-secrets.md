# Users, keys, secrets and certificates

Operator reference for preparing deploy credentials without mixing products or
placing secret values in the repo. See the Codex instructions at
`.codex/skills/cloudru-management/references/evolution-services.md`.

## Principal map

| Credential | Consumer | Scope / expiry | Must stay out of |
|---|---|---|---|
| IAM Key ID + Secret | Evolution Public APIs | Assign org/project/service roles to service account; exchange for approx. 1-hour IAM Bearer token | Alice runtime, issue body, shell trace |
| `EDS_API_KEY` + project UUID | EDS v0.4.0 | Repo/Workflow Studio product key, `X-API-KEY`; use `master` explicitly | IAM fields, image, Git remote URL, output |
| Workflow Studio direct API | Direct product REST requests | IAM auth per published Workflow Studio API | EDS config and runtime env |
| Gateway static API key | Machine-to-machine callers where selected by Gateway policy | A gateway-client credential; exact key lifecycle is per API-key page | Browser JavaScript, public config, GitHub variable summary |
| Foundation Models API key | Model inference | Issued for Foundation Models on a service account; expiry 1 day–1 year; docs recommend a moderate interval such as 90 days | IAM/EDS slots, user session, trace |
| S3 access key pair + tenant ID | Object Storage S3 client | Separate storage principal; restrict bucket/prefix/operations | PostgreSQL password, FM key |
| Alice short token / GitHub OAuth secret | Alice's own auth middleware | Application-level identity; owner IDs checked in backend | Cloud API key slots |
| PostgreSQL role password | `alice_app` over TLS | One DB, non-superuser, max connections; rotate as a DB operation | URLs in stdout/process args/issues |
| Certificate key | TLS terminator | Gateway custom hostname certificate | JSON payload, Docker image, CI log, tracked files |

Cloud console humans, service accounts, Gateway API-key clients and Alice browser
accounts are separate identity concepts. Current API Gateway documentation is
inconsistent about Basic Auth: release notes say old internal-user authorization
was removed, while the policy guide still documents it. Do not rely on Basic Auth
until the current console/API confirms it is supported for this Gateway; prefer
the documented IAM/API-key policy or supported OIDC. Delete/disable a human Cloud
user only through IAM with an account owner; do not use shared human credentials
for automation. Prefer a scoped service identity for service-to-service API calls;
don't expose a static secret in Alice frontend.

## Secret Manager inventory (names/IDs only)

Use Secret Management for deploy-time secrets after adapter #477 and pipeline
read path are available. `scsm.user` is documented for listing/secrets metadata;
`scsm.admin` adds secret create/update/delete/new version. Confirm that the chosen
role can read the **payload**, not just metadata, with a minimal test identity.
Do not grant administrator to the Alice process merely to rotate its secrets.

Initial logical entries; actual service IDs/version syntax must come from the
current API/console and are deliberately not fabricated here:

- `alice/runtime/short-token`
- `alice/runtime/postgres-dsn`
- `alice/runtime/provider-credential-key` (preserve existing value at migration)
- `alice/runtime/github-oauth-client-secret`
- `alice/runtime/foundation-models-api-key` (if managed-key path is selected)
- `alice/control-plane/iam-key-secret`
- `alice/control-plane/eds-api-key`
- `alice/backup/postgres-role-password`

Record resolved secret IDs/version IDs and consuming app revision in a protected
operator inventory; issue/config/log output should contain only secret name and
version ID. Pin a known-good version for each app revision if supported. Rotate:
create new version, grant/read-test, deploy new revision, verify auth/data/logs,
then retire old version after rollback window. Preserve `ALICE_PROVIDER_CREDENTIAL_KEY`
through restore. A newly generated value would strand encrypted provider records.

## Certificate references

Use Certificate Manager for the API Gateway custom hostname. Verify domain SAN,
certificate status/expiry, Gateway binding and renewal. Cloud.ru's Certificate
Manager can issue Let's Encrypt certificates after DNS validation/delegation or
store imported certificate versions. Never put the private key in this repo.

PostgreSQL `sslrootcert` has a separate trust job: runtime needs the public root
CA to verify the DB server. The PostgreSQL host retains its private server key.
If mTLS is later required, manage the client certificate/private key through a
separate protected runtime path and server-side role mapping. No mTLS client
certificate feature is configured by this kit.

User-facing TLS sequence: `Gateway custom domain → matching Certificate Manager
certificate → CNAME custom FQDN to <gateway UUID>.apigw.cloud.ru → validate DNS,
TLS/SAN, GitHub OAuth callback and MCP protected-resource metadata → switch app
origin`. Container Apps itself has a generated public address and does not bind a
custom host today.
