# SSH Runtime

Logos Runtime executes commands and file operations on remote Linux hosts through the system OpenSSH client.

## Identity model

The Runtime does not introduce a second permission system. A configured Logos identity maps to a Linux username, and the SSH process runs as that account. Linux filesystem permissions, groups, sudo policy, capabilities, and service permissions remain authoritative.

Targets are named server-side. The model receives a target name, not arbitrary SSH credentials or an arbitrary host. The Linux user is normally resolved from the trusted Logos identity; the tool does not ask the model to choose a Linux account.

Example configuration:

{
  "preview": {
    "host": "preview.example",
    "port": 22,
    "default_user": "logos-agent",
    "allowed_users": ["logos-agent", "deploy"],
    "identity_map": {
      "<logos-owner-id>": "logos-agent"
    },
    "workspace_root": "/srv/logos",
    "identity_file": "/srv/logos/keys/preview",
    "known_hosts": "/srv/logos/ssh/known_hosts"
  }
}

Set it through LOGOS_SSH_TARGETS_JSON. Host-key verification requires known_hosts either per target or through LOGOS_SSH_KNOWN_HOSTS.

The Logos application image must provide the OpenSSH client. The remote Linux target must provide the POSIX tools used by Runtime plus GNU coreutils timeout.

## Tools

- ssh_runtime_exec: execute a remote shell command. High risk and requires approval.
- ssh_runtime_read_file: read a remote absolute path. Read-only and does not require approval.
- ssh_runtime_write_file: atomically write a remote absolute path. High risk and requires approval.

## Security rules

- No arbitrary SSH credentials are accepted from tool arguments.
- StrictHostKeyChecking=yes is always used.
- An explicit known_hosts file is required.
- Linux username is validated and resolved from the trusted identity mapping.
- File operations are confined to workspace_root when configured.
- Tool execution never enables sudo implicitly.
- Remote commands run under a minimal environment and a server-side timeout.
- File writes use a remote temporary file and mv so failed transfers do not replace the destination.
- Tool results contain host/user metadata but never private key contents.
- Runtime operations emit lifecycle events into the active Execution Trace.

## Preview

A Preview must use a real configured non-production SSH target, verify the Linux user with id -un, write a test file, read it back, and clean it up. Production hosts and credentials must not be reused by Preview.
