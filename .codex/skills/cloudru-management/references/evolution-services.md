# Evolution services, identities and configuration boundaries

This reference supplements `eds.md` and `deploy/cloudru/README.md` for the
requested Cloud.ru Evolution stack. Verify the current service OpenAPI spec,
roles and lifecycle instructions before a write. Templates in `deploy/cloudru`
are plans/API examples, not Terraform or an `eds apply` manifest.

Contents: [credentials](#do-not-mix-identities-or-credentials) ·
[Workflow](#workflow-two-separate-apis) · [DNS/Gateway/TLS](#dns-tls-and-api-gateway) ·
[logs](#logs-and-audit-records) · [Secret/KMS](#secret-management-and-key-management) ·
[certificates](#certificate-manager) · [AI](#ai-services-in-evolution).

## Do not mix identities or credentials

| Identity / credential | What it authenticates | How it is sent / stored |
|---|---|---|
| Cloud.ru organization user | Human console/API access, assigned org/project/service roles | Personal account; give only required project/service roles |
| Cloud.ru service account | Automation principal scoped to org or one project | Assign resource service roles, then issue matching access key/token |
| Evolution IAM Key ID + Key Secret | Exchanges at IAM for short-lived Bearer token; many service REST APIs | `CLOUDRU_IAM_KEY_ID` / `CLOUDRU_IAM_KEY_SECRET` only in control plane; IAM token lifetime documented as 1h |
| EDS `EDS_API_KEY` | Evolution DevServices `eds repo` / `eds wf` CLI | Product API key in `X-API-KEY`, paired with `EDS_PROJECT_ID`; **not** IAM key pair |
| Standalone Workflow Studio Public API | Direct Workflow Studio API | Service-account IAM auth and Bearer token, per Workflow Studio API docs; EDS CLI has its own DevServices auth |
| API Gateway static API key | One API client allowed by Gateway policy | Send Key Secret in the expected header from protected clients; never embed one shared secret in Alice browser code |
| Foundation Models API key | Model API for a configured service account | A service-specific secret, separate from IAM/EDS; keep behind Alice provider-credentials boundary |
| Object Storage S3 access pair | S3 object operations | Dedicated storage Key ID/Secret + tenant/region/bucket scope; not a Foundation Models key |
| Alice GitHub OAuth client secret | Alice owner login with immutable GitHub account ID allowlist | Runtime app secret; different identity plane from Cloud.ru IAM |
| PostgreSQL role/password | Alice runtime SQL access | Dedicated database role and TLS-verified DSN; do not reuse Cloud.ru credentials |
| Certificate private key | TLS termination for a custom domain or database | Keep inside Certificate Manager/origin host/approved secret flow; never source, log, issue or `env` example |

Do not print tokens, secrets, Authorization headers or raw config. Prefer short-lived
IAM tokens to long-lived static keys where the service supports it. Record
principal name, owner, service/project scope, purpose and rotation date in a
separate secret-free inventory. Rotate by issuing a replacement, verifying the
consumer and logs, then revoking the old credential after overlap. Do not revoke
an existing key as an unrequested cleanup.

Cloud IAM roles are distinct from application users. Evolution service accounts
have a scope selected at creation (org or project); AI Agents docs say this scope
cannot be changed later. Do not grant project administrator to an app runtime
when a service-specific invoker/read role works. Alice's GitHub user, browser
session, conversation owner and MCP OAuth claims continue to authorize Alice
requests even when a Gateway or service account authenticated the infrastructure.

## Workflow: two separate APIs

- **Evolution DevServices CLI**: use `eds repo` / `eds wf`; EDS-specific product
  key is `EDS_API_KEY` (`X-API-KEY`). v0.4.0 `eds wf app create` immediately runs
  its first deploy. See `eds.md` for exact flags, pagination and SHA limitations.
- **Direct Workflow Studio Public API**: docs identify `https://pipeline.cloud.ru`
  product APIs and IAM Bearer authentication. Its IAM credentials/token are not
  interchangeable with the EDS CLI product key. Confirm the current OpenAPI path,
  resource IDs and operation before calling it.
- Workflow Studio deployment pipeline is distinct from **AI Workflows** in AI
  Agents. Do not route an Alice application deploy into an AI agent workflow.

Before enabling a Workflow Studio pipeline, establish how it injects runtime
secrets, PostgreSQL CA, registry identity and exact committed source digest
before it publishes. EDS create/deploy accepts a branch; branch name alone does
not prove the image matches an approved commit.

## DNS, TLS and API Gateway

- Cloud DNS management endpoint documented at
  `https://console.cloud.ru/api/clouddns`; service operations use an Evolution IAM
  Bearer token. The zone API needs the Cloud DNS Service Instance ID (`parentId`),
  which differs from the Cloud project UUID. List/inventory first.
- Container Apps currently issues a provider URL, not a custom domain. Shared
  API Gateway supports a system `*.apigw.cloud.ru` domain and custom domain via
  Certificate Manager. Gateway domain validation uses CNAME to the gateway system
  domain. See `deploy/cloudru/dns/README.md` and `deploy/cloudru/api-gateway/README.md`.
- Gateway OpenAPI supports provider `x-cloud-*` extensions including backend,
  rate counter and log-group references. Its default public address does not
  authenticate a caller. Alice must continue its GitHub/short-token/MCP OAuth
  checks; Gateway IAM auth or a static API key cannot replace that identity.
- No cache for Alice user/chat/files/voice/MCP endpoints. Streaming and WebSocket
  behavior require route-specific tests; a regular HTTP proxy/read timeout may
  cut a response.
- Certificate Manager controls certificate lifecycle and can issue Let's Encrypt
  certificates. Attach a certificate to a custom Gateway domain; Container Apps
  itself does not accept a custom host today. Never route public TLS private key
  through a repo config. Database TLS trust is separate: the Alice client must
  pin the database's trusted CA and verify the PostgreSQL server name.
- Before DNS/CA changes, preserve existing records and verify domain ownership.
  Changing authoritative NS or apex routing can affect MX, mail verification and
  unrelated services. Keep a secret-free before/after snapshot and rollback value.

## Logs and audit records

Keep these streams separate:

| Stream | Source | What it proves |
|---|---|---|
| App logs | Container stdout/stderr (JSON planned in #478) | Application events; redact before output |
| Container system logs | Container Apps runtime | Start, scale and platform events; console view shows latest 1000 entries, older data in logging service |
| HTTP request logs | Explicit Container Apps request-logging option or API Gateway `x-cloud-log-group` | Traffic metadata; inspect field selection and sensitive headers/query/body before enabling |
| Cloud audit logs | Audit-logging service | Who changed cloud control-plane resources and when |
| AI service telemetry | AI Agents related Logaas/Monas/Audaas integration | Agent logs, metrics and audit events, in the respective services |
| EDS job logs | Workflow Studio/EDS jobs | Deployment output; may contain sensitive build variables; capture privately and summarize/redact |

A log-group UUID is not a redaction policy. First test with harmless synthetic
secret markers and confirm no request body, Authorization, cookie, query token,
private key or user prompt leaks. Trace/request IDs help correlate logs but are
not auth. Keep retention/access restricted and test alert delivery separately.

## Secret Management and Key Management

Secret Management supports versioned secrets. Use a stable explicit version for
a deploy/rollback when the consumer supports it; do not assume mutable `latest`
is reproducible. Current role docs list `scsm.admin` and `scsm.user`; they give
admin create/update/delete/new-version privileges and user list/read-metadata
privileges. Verify exact access to secret payload/version in the current role
matrix before choosing the runtime identity. Runtime read must never imply admin
rotation/delete.

Key Management protects cryptographic operations/material; it does not replace
TLS between Alice and PostgreSQL. Separate `sckm.admin` key lifecycle duties from
runtime encrypt/decrypt user rights (`sckm.user`) when the service's current
operation matrix permits. First integrate it with encrypted PostgreSQL backups
in S3, then test decrypt/restore before changing keys. No plaintext fallback.

Secret Management and KMS adapters are tasks #477 and #479. Until a supported
runtime provider adapter exists, do not invent a `secretRef`, CSI mount or
environment variable. In `container-app.create.example.json`, a field with
`value: REPLACE_FROM_SECRET_STORE` is an unresolved marker; current payload
requires the deployment adapter to resolve the secret before sending the app
request. It is **not** a working native reference. Keep the version ID separate
from value, and prevent secret bodies from entering HTTP traces or CI artifacts.

## Certificate Manager

Current roles are `ccm.admin` and `ccm.user`; separate certificate lifecycle from
runtime use. The service supports imported certificates and Let's Encrypt; API
is under `https://certificatemanager.api.cloud.ru` and uses Evolution IAM auth.
A certificate must match the exact Gateway custom FQDN (wildcards only cover
matching subdomains). Keep issued/private key material inside the service and use
version/status/expiry metadata in inventory. Imported certificates support
additional versions; Let's Encrypt uses its managed lifecycle and is not manually
versioned like imported certs. Test renewal/expiry notification and Gateway binding.

Do not copy the PostgreSQL root/client CA private material into Certificate
Manager unless that precise use is supported and intended. Public trust anchors,
server certificates and private signing keys have different confidentiality
requirements.

## AI services in Evolution

| Service | Role and integration | Guardrail / cost boundary |
|---|---|---|
| Foundation Models | Model API endpoint documented as `https://foundation-models.api.cloud.ru/v1`; service-account API key. Alice currently has a provider integration; preserve its primary-model behavior and encrypted key store |
| Foundation Models Guardrails | Input/output sensitive-data masking/restore and monitoring alerts; test model prompts, traces, tool arguments and cross-user isolation |
| AI Agents | Public APIs can manage agents, agent systems and MCP servers; use service invoker/viewer roles for runtime calls and admin only for lifecycle |
| Agents Space | Separate user-facing agent workspace built on EvoClaw; depends on AI Agents, Foundation Models and Object Storage. Keep it a pilot, not an implicit Alice runtime migration |
| EvoClaw / AI Workflows | Distinct AI-agent runtime/workflow concepts within AI Agents. Preserve Alice's `UniversalToolExecutor`, approvals and ExecutionTrace when testing |
| Distributed Train | ML jobs/experiment tracking have separate quotas and credentials. Tracking SDK availability does not make GPU training free |
| Artifact Registry | Container images/packages used by services are billed/lifecycle-managed separately; deploy by immutable OCI digest |
| Logaas / Monas / Audaas | Logs, metrics and audit for AI entities, separate from Container Apps stdout and project audit |

Agents Space pricing is pay-as-you-go: current docs list baseline 2 vCPU + 4 GB
RAM at 3.84 ₽/hour **per running agent**, plus model tokens and Object Storage
operations/egress. Stop an unused agent; do not treat Preview or a free storage
allowance as a free-running AI worker. Recheck tariff at start time.

The AI Agents quick start requires both Foundation Models and ML Inference for
its documented agent creation scenario. AI Agents docs also describe its use of
Artifact Registry, Object Storage, Secret Management, centralized logs/metrics,
audit and cost controls. Creating agents or GPU-backed workers can incur costs
in those dependent services. Begin with one isolated, least-privileged test agent;
no cloud resources are created by this skill.

Sources: [Evolution roles](https://cloud.ru/docs/evolution/overview/topics/roles),
[EDS source](https://github.com/cloud-ru/evolution-devservices-cli/tree/v0.4.0),
[Workflow auth](https://cloud.ru/docs/pipeline/ug/topics/api-ref__authentication),
[DNS API auth](https://cloud.ru/docs/clouddns/ug/topics/api-ref_authentication),
[Secret roles](https://cloud.ru/docs/scsm/ug/topics/access),
[Certificate Manager](https://cloud.ru/docs/certificate-manager/ug/topics/access),
[AI Agents roles](https://cloud.ru/docs/ai-agents/ug/topics/access),
[Agents Space pricing](https://cloud.ru/docs/agents-space/ug/topics/pricing),
[Guardrails](https://cloud.ru/docs/foundation-models/ug/topics/concepts__guardrails).
