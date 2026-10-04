---
name: cloudru-management
description: "Prepare and operate Alice Pro on Cloud.ru Evolution: EDS and Workflow Studio, Container Apps, external PostgreSQL, Cloud DNS, API Gateway, IAM identities and keys, Secret and Certificate Management, logs, Object Storage, and the optional Evolution AI service catalog. Alice's current AI backend remains Yandex Cloud / Yandex AI Studio. Use for configuration, read-only inventory, readiness checks, deployments, and troubleshooting."
---

# Cloud.ru / Alice Pro

Read repository `AGENTS.md` and the current issue first. Work from fresh `master`
in a focused branch; publish a PR. Keep existing authorization in scope: do not
ask again for authorized read-only discovery, code or configuration work. Apply
the repository's production/data/secret approval rules to actual live changes.

## Start here

1. Identify the interface in the table below. Do not interchange keys.
2. Read [EDS reference](references/eds.md) for CLI work, or the
   [deployment runbook](../../../deploy/cloudru/README.md) for Alice rollout.
3. Read [Evolution service and identity reference](references/evolution-services.md)
   for DNS, Gateway, workflow, logs, users/keys, secrets/certificates and AI.
   For JSON stdout and cloud log-stream configuration, also read
   [`deploy/cloudru/logging/README.md`](../../../deploy/cloudru/logging/README.md).
   Reuse existing authorized credentials without printing their values. Report
   missing variable **names**. Do not dump environment, config, DSNs or raw API
   responses. `eds config` exposes key prefixes/suffixes; skip it in agent logs.
4. Run read-only inventory with complete pagination. Confirm the project,
   resource IDs and current state; a similar name is insufficient evidence.
5. Complete templates and run `scripts/cloudru_deploy_preflight.py`. Its success
   validates configuration, not infrastructure, costs or deployment readiness.
6. For rollout, record approved source SHA → image digest → deployment/revision
   ID; verify authentication, database TLS and application behavior. Report
   blockers precisely; never call prepared templates a completed deployment.

## Choose the interface

| Interface | Purpose | Authentication |
|---|---|---|
| `eds repo`, `eds wf` v0.4.0 | Repo and Workflow Studio | `EDS_API_KEY` as `X-API-KEY`, `EDS_PROJECT_ID` |
| Evolution service APIs | Container Apps, IAM and other documented services | Service-specific auth; IAM Key ID/Key Secret where supported |
| Existing Alice backend | IAM wizard, provider keys, S3, Container Apps | Existing provider adapters and redaction boundaries |
| Cloud CLI Advanced | Advanced platform only | Its own AK/SK; not an Evolution IAM pair |

EDS is **not** a universal infrastructure provisioner. It has no PostgreSQL,
KMS, Secret Management, bucket or Container Apps resource-configuration commands.
`eds wf app create` **immediately deploys**. `eds wf app deploy` selects a branch,
not an immutable commit; enforce SHA/digest checks in the pipeline.

## Deployment constraints

- Alice runs in Container Apps. Do not introduce a replacement VM/Kubernetes
  runtime, Remote Desktop Commander or Supabase into this task.
- Target durable data: external PostgreSQL with a public endpoint, certificate
  verification and verified narrow client CIDRs. Evolution Managed PostgreSQL
  currently has private connectivity; Container Apps cannot connect directly.
- A stable Container Apps outbound address is **not established**. Do not treat
  one observed IP as a guarantee or open PostgreSQL to the internet to bypass
  this dependency. See [PostgreSQL runbook](../../../deploy/cloudru/postgres/README.md).
- Keep scale at 0–1 initially. This does not prove absence of revision overlap
  or solve distributed background-job/migration coordination.
- Object Storage holds objects/backups, not the live PostgreSQL data directory.
  Preserve local/Termux SQLite compatibility.
- Keep owner login, MCP OAuth, `InvocationContext` and `ExecutionTrace` intact.
  Gateway API keys do not replace application user identity or authorization.

## Secret and output boundaries

- Use env injection or an authorized secret store; examples contain placeholders
  only. Never pass keys in CLI arguments or embed them into Git URLs.
- In EDS v0.4.0 HTTPS `repo clone`/`remote-add` embed the key into the remote URL.
  Use an already authorized SSH setup or an approved credential helper instead.
- Capture EDS output privately and publish only validated resource IDs/status.
  JSON formatting is not redaction. Job logs and HTTP errors may contain secrets.
- Separate deployment IAM/EDS credentials from runtime DB, S3 and model keys.
  Preserve `ALICE_PROVIDER_CREDENTIAL_KEY` across redeploys and restore.
- New secret/KMS integrations must use provider boundaries and redact before
  tracing. Do not assume an adapter or env variable exists until checked in code.

## Other Evolution tasks

Use `cloudru_iam.py`, `cloudru_iam_routes.py` and `provider_key_rotation.py` for
existing key flows. Consult `docs/integrations/cloudru-iam-wizard.md` and
`docs/provider-key-rotation.md` before changing them.

Our zones live in Evolution DNS: use `https://dns.api.cloud.ru` with an
Evolution IAM Bearer token (`https://console.cloud.ru/api/clouddns` is the
separate classic Cloud DNS service). Evolution API Gateway has no public
management API; do not automate against it.
Discover zones and records first; the DNS service `parentId` is its Service
Instance ID, not the project UUID. Follow
[`deploy/cloudru/dns/README.md`](../../../deploy/cloudru/dns/README.md) and
preserve the zone/record snapshot before any authorized write. Do not reuse EDS
or Cloud Advanced credentials as Evolution IAM credentials.

For the requested service rollout and current pricing caveats, read the
[service matrix](../../../deploy/cloudru/services.md). Recheck official
documentation before live provisioning; Preview does not make dependent
compute, storage or traffic universally free.
