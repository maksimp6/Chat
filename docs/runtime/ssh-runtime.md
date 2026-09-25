# SSH Runtime settings

Alice Pro keeps SSH Runtime configuration as a server-side structured setting under the `configs` table.

## Configuration source

When no saved configuration exists, the Runtime bootstraps from:

- `ALICE_SSH_TARGETS_JSON`
- `ALICE_SSH_KNOWN_HOSTS`

After the settings are saved through the UI/API, the persisted configuration becomes authoritative.

## Safety defaults

SSH Runtime is disabled unless a target configuration exists or it is explicitly enabled in saved settings.

Strict host-key verification is mandatory. Every enabled target must have either a target-level `known_hosts` reference or the global `known_hosts` reference.

Private key material is never accepted by the settings API. `identity_file` is only a server-side path/reference to a key already present on the server.

Read-only mode disables command execution and file writes.

Command execution is fail-closed behind `command_allowlist`: when command execution is enabled, at least one full-match regex must be configured. Privileged commands (`sudo`, `su`, `doas`, `pkexec`) are disabled by default and require both explicit enablement and the normal approval gate when configured that way.

File writes require approval by default. These policies are persisted with the Runtime settings and settings changes emit sanitized `ssh_runtime_settings_updated` events in Execution Trace.

Command output is bounded by `max_output_bytes` to prevent an SSH call from exhausting memory or flooding the trace.

## Settings API

`GET /api/ssh-runtime/settings`

Returns the current structured SSH Runtime settings and the last connection-test result. The endpoint requires a trusted Alice owner identity.

`PUT /api/ssh-runtime/settings`

Validates and persists the complete settings object. The endpoint does not accept private-key contents.

`POST /api/ssh-runtime/test`

Body:

```json
{"target":"preview"}
```

The target must already exist in the saved configuration. The test executes only the fixed remote command `true`, using the trusted Alice identity mapping.

## Example

```json
{
  "enabled": true,
  "read_only": false,
  "allow_command_execution": true,
  "allow_write_operations": true,
  "max_output_bytes": 1048576,
  "command_allowlist": [
    "^id(?:\\s+-un)?$"
  ],
  "allow_privileged_operations": false,
  "approval_required_for_write": true,
  "approval_required_for_privileged": true,
  "known_hosts": "/srv/alice/ssh/known_hosts",
  "targets": {
    "preview": {
      "host": "preview.example",
      "port": 22,
      "default_user": "alice-agent",
      "allowed_users": ["alice-agent"],
      "identity_map": {
        "<alice-owner-id>": "alice-agent"
      },
      "workspace_root": "/srv/alice",
      "identity_file": "/srv/alice/keys/preview",
      "connect_timeout_seconds": 5,
      "command_timeout_seconds": 30,
      "max_output_bytes": 1048576
    }
  }
}
```

The values above are illustrative. Production credentials and host keys must come from deployment configuration/secrets management.

## Workflow integration

The development workflow should resolve and validate SSH Runtime settings before selecting a remote execution environment. Consequential operations remain subject to the existing tool approval boundary and are recorded in Execution Trace.
