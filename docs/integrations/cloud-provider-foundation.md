# Cloud provider foundation (issue #417)


## Architecture

- Provider-neutral interfaces live in `cloud/base.py`, `cloud/registry.py`, and `cloud/models.py`.
- Cloud tools are declared in `cloud/tools.py` and registered in `ToolRegistry` under the `cloud` category.
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



Set service endpoints and paths for the domains you want to enable:

- optional inventory domains:

Authentication:


If an endpoint/path is missing, capability discovery reports it as disabled and calls fail with `unsupported_capability`.

## Extending to another provider

1. Implement `CloudProvider` contract from `cloud/base.py`.
2. Register the provider in `cloud/registry.py`.
3. Reuse `cloud/tools.py` unchanged unless provider-specific parameters are required.
4. Keep trace payloads sanitized before `trace.add_event`.
