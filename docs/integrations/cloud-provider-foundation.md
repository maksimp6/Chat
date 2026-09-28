# Cloud provider foundation (issue #417)

Alice Pro now exposes a provider-neutral cloud tool surface backed by a Cloud.ru implementation.

## Architecture

- Provider-neutral interfaces live in `cloud/base.py`, `cloud/registry.py`, and `cloud/models.py`.
- Cloud tools are declared in `cloud/tools.py` and registered in `ToolRegistry` under the `cloud` category.
- Cloud.ru implementation lives in `cloud/cloudru/client.py` and `cloud/cloudru/provider.py`.
- SSH execution inside VMs is exposed as `cloud.ssh.exec` and delegated to `runtime_tools.ssh_runtime_exec`.
- Infrastructure API operations (`cloud.compute.*`, `cloud.resources.*`, `cloud.logs.query`, `cloud.metrics.query`, `cloud.backup.*`, `cloud.costs.summary`) are separate from SSH runtime operations.

## Safety and confirmation

- Destructive/cost-creating operations require explicit tool approval:
  - `cloud.compute.start|stop|reboot`
  - `cloud.backup.create`
  - `cloud.ssh.exec`
  - `cloud.environment.create|start|stop|delete|exec`
- Read-only operations are marked `read_only=true` and do not require approval.

## Sandbox environments (issue #445, phase 3 of #440)

`environment_manager.py` selects one `EnvironmentAdapter` (`cloud/environment.py`)
per environment record:

- `local` (default) — today's git worktree + in-process thread runtime. Unchanged
  behavior; Termux/local deployments do not need any new configuration.
- `cloudru` — an isolated Cloud.ru Container Apps Job or Compute VM, provisioned
  through `cloud/cloudru/provider.py` (`environment_create/start/exec/stop/delete`)
  and `cloud/cloudru/environment_adapter.py`. Use this instead of `local_runtime_exec`
  (local subprocess) or `cloud.ssh.exec` into a shared VM when Alice needs a fully
  isolated, disposable sandbox.

Every environment carries a TTL (`ttl_seconds`/`expires_at`). Local environments keep
no TTL by default (set `ALICE_ENV_DEFAULT_TTL_SECONDS` to opt in); Cloud.ru sandboxes
default to 3600s (override with `ALICE_CLOUDRU_ENV_DEFAULT_TTL_SECONDS`, or pass
`ttl_seconds` explicitly on create). `environment_manager.cleanup_expired_environments()`
deletes expired environments; it runs opportunistically from `list_environments()`, and
can also be wired into an external cron/scheduler that calls it directly.

### Configuration

- `CLOUDRU_ENVIRONMENT_ENDPOINT`, `CLOUDRU_ENVIRONMENT_PATH` — base Cloud.ru API for
  sandbox lifecycle calls (Container Apps Jobs or Compute VM), reusing the same
  `CLOUDRU_API_KEY` / `CLOUDRU_IAM_KEY_ID`+`CLOUDRU_IAM_KEY_SECRET` authentication as
  the rest of this provider.
- `ALICE_ENV_ADAPTER` — default adapter for `create_environment()` calls that don't pass
  `adapter=` explicitly (default `local`).

### e2e scenario: create → execute → delete

This cannot run against the real Cloud.ru API in CI (no paid resources are created in
CI); it is covered by mocked unit tests (`tests/test_cloudru_environment_adapter.py`,
`tests/test_environment_adapters.py`, `tests/test_cloud_tools.py`) and documented here
for manual verification against a real Cloud.ru project:

1. Set `CLOUDRU_ENVIRONMENT_ENDPOINT`/`CLOUDRU_ENVIRONMENT_PATH` and Cloud.ru
   credentials.
2. Create a sandbox from a commit: `cloud.environment.create` with
   `{"branch": "master", "ttl_seconds": 3600}` (requires approval). This calls
   `CloudRuProvider.environment_create`, which returns a remote resource id persisted
   as `cloud_resource_id` on the environment row.
3. Start it: `cloud.environment.start` with `{"environment_id": "<id>"}` (requires
   approval).
4. Run a command: `cloud.environment.exec` with
   `{"environment_id": "<id>", "command": "echo hello"}` (requires approval); stdout/
   stderr are redacted from traces via `trace_redact_result_fields`.
5. Delete it: `cloud.environment.delete` with `{"environment_id": "<id>"}` (requires
   approval). This calls `CloudRuProvider.environment_delete` and removes the DB row.

Expired sandboxes that were never explicitly deleted are removed automatically by
`cleanup_expired_environments()` once their TTL passes.

## Trace and secret handling

- Cloud API request/response trace events are recorded with sanitized payloads.
- Authorization values are never written into trace event payloads.
- Error mapping returns provider-safe codes (e.g. `authorization_failed`, `provider_http_error`) without exposing credentials.

## Cloud.ru configuration

Cloud.ru capabilities are intentionally incremental and endpoint-driven.

Set service endpoints and paths for the domains you want to enable:

- `CLOUDRU_COMPUTE_ENDPOINT`, `CLOUDRU_COMPUTE_PATH`
- `CLOUDRU_OBSERVABILITY_ENDPOINT`, `CLOUDRU_OBSERVABILITY_LOGS_PATH`, `CLOUDRU_OBSERVABILITY_METRICS_PATH`
- `CLOUDRU_BACKUP_ENDPOINT`, `CLOUDRU_BACKUP_PATH`
- `CLOUDRU_BILLING_ENDPOINT`, `CLOUDRU_BILLING_SUMMARY_PATH`
- optional inventory domains:
  - `CLOUDRU_STORAGE_ENDPOINT`, `CLOUDRU_STORAGE_PATH`
  - `CLOUDRU_NETWORK_ENDPOINT`, `CLOUDRU_NETWORK_PATH`
  - `CLOUDRU_DATABASE_ENDPOINT`, `CLOUDRU_DATABASE_PATH`
  - `CLOUDRU_KUBERNETES_ENDPOINT`, `CLOUDRU_KUBERNETES_PATH`
  - `CLOUDRU_SECURITY_ENDPOINT`, `CLOUDRU_SECURITY_PATH`

Authentication:

- runtime API key: `CLOUDRU_API_KEY`, or
- IAM key-pair fallback: `CLOUDRU_IAM_KEY_ID`, `CLOUDRU_IAM_KEY_SECRET`

If an endpoint/path is missing, capability discovery reports it as disabled and calls fail with `unsupported_capability`.

## Extending to another provider

1. Implement `CloudProvider` contract from `cloud/base.py`.
2. Register the provider in `cloud/registry.py`.
3. Reuse `cloud/tools.py` unchanged unless provider-specific parameters are required.
4. Keep trace payloads sanitized before `trace.add_event`.
