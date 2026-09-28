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
- Read-only operations are marked `read_only=true` and do not require approval.

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
