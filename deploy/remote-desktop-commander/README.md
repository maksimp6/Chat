# Remote Desktop Commander beside preview

This directory is the standalone Docker configuration for the official
`@wonderwhy-er/desktop-commander` Remote Device, version **0.2.52**.
It is separate from the custom Chromium inspection worker in PR #719.

The image installs Node 22, Python, git, SSH client and system ripgrep. npm lifecycle
scripts and Puppeteer's browser download are disabled; the package's documented
`remote --help` is executed during the build to verify the installed CLI.
The package and transitive npm dependencies are locked and installed with `npm ci`.
The lock is generated from npm for the exact CLI version. Security overrides pin
sharp 0.35.4 and ExcelJS's UUID 11.1.1; the image build exercises PNG decoding and
an XLSX write/read round trip to verify these dependency APIs remain usable.
The base image and Debian packages still float. Record the deployed image ID. This is a files/terminal agent inside
a container, not a full graphical desktop or automatic browser login.

## Docker layout

On the server: `$HOME/alice-preview/services/remote-desktop-commander/`.

- `Dockerfile`, `compose.yaml`, `entrypoint.sh`, `config.json`: versioned configuration.
- `state/`: persistent agent home, OAuth credentials and local tool history.
- `workspace/`: the only host directory exposed for ordinary work.
- `releases/`: clean configuration archive per deployment attempt.

The service runs as uid/gid 1000, with a read-only root filesystem, dropped
capabilities, no Docker socket, no host root or preview credentials mounted, and
no published ports. A one-shot initializer has only CHOWN and FOWNER capabilities,
no network, and the same two mounts; the running agent keeps all capabilities
dropped. CI exercises both initialization and non-root writable volumes.
It connects outbound to the official relay. The directory
allowlist is a tool setting, not an OS sandbox; container mounts are the access
boundary. The agent can run commands and access its own saved auth state: never
ask it to read or return credentials. `state/` is owner-only on the host.

## Deployment

After protected merge, run **Remote Desktop Commander deployment** on `master`:
`preflight`, then `install`, then `status`. It uses the existing production
`PREVIEW_SSH_*` secrets with strict host-key checking. Installation changes only
this service and its dedicated directories; it does not run preview/production
scripts or restart Traefik. Repeating installation keeps state and workspace.
The install check proves Node can run, not that the device is paired or online.
No production secrets are available to PR code.

## Pairing — separate from installation

Official references:

- https://github.com/desktop-commander/remote-desktop-commander/blob/main/docs/SETUP.md
- https://github.com/wonderwhy-er/DesktopCommanderMCP/blob/v0.2.52/src/remote-device/README.md

First start uses the official OAuth device flow. A verification URL and matching
code are printed locally by the agent. View these through a trusted SSH session
(`docker compose logs commander` in the service directory), never publish them
in Actions logs, issues, traces or artifacts. Approve that exact device in the
same account used by the already-connected Remote Desktop Commander plugin.
Pairing creates persistent remote access and is a separate owner-authorized step.
Do not store a pairing code in GitHub Secrets or invent an API-token shortcut.

The saved session is in `state/.desktop-commander-device/device.json`; upstream
creates it with mode 0600. The container restarts and normally reuses the session.
Only after `list_devices` reports this device online and a tool call succeeds
should connection be reported complete. Currently only the user's offline `mint`
device has been observed; Cloud.ru connectivity has not been verified.

Stop with `docker compose stop` to retain state. Device revocation is performed
in the official dashboard when requested. No automatic logout, deletion, or
revocation is part of deployment.
