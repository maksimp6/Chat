# Codex environment bootstrap

`scripts/codex_setup.sh` prepares a fresh Codex container and
`scripts/codex_maintenance.sh` refreshes a cached one. Setup secrets disappear
before the interactive agent phase, so both scripts source
`scripts/codex_agent_credentials.sh` to install only the credentials explicitly
provided by the Codex environment.

| Purpose | Preferred variable | Compatible variables |
| --- | --- | --- |
| GitHub fine-grained token | `CODEX_GITHUB_TOKEN` | `GITHUB_TOKEN` |
| GPG private signing key | `CODEX_GPG_PRIVATE_KEY` | `GPG_PRIVATE_KEY` |
| SSH private key | `CODEX_SSH_PRIVATE_KEY` | `SSH_PRIVATE_KEY`, `PREVIEW_SSH_PRIVATE_KEY` |
| Complete SSH known-hosts file | `CODEX_SSH_KNOWN_HOSTS` | `SSH_KNOWN_HOSTS`, `PREVIEW_SSH_KNOWN_HOSTS` |

When a complete known-hosts value is not supplied, the helper also accepts the
two pinned server pairs `CLOUD_RU_SERVER_1`/`CLOUD_RU_SERVER_1_pub` and
`CLOUD_RU_SERVER_2`/`CLOUD_RU_SERVER_2_pub`. Each `_pub` value must be the SSH
host public key, not a user key. The helper never runs `ssh-keyscan`; host trust
must come from configured evidence.

The imported GPG fingerprint becomes Git's `user.signingkey`. The SSH private
key and known-hosts file are written with mode `0600`, and the GitHub CLI stores
its authenticated host configuration with mode `0600` before configuring Git's
credential helper. Secret handling temporarily disables shell tracing and does
not print key or token values.

Credential variables may be omitted for ordinary read-only bootstrap runs. If a
credential is provided but invalid, setup fails rather than leaving a partially
configured signing or SSH identity.
