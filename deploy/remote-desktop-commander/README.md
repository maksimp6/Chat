# Remote Desktop Commander beside preview

This directory is the standalone Docker configuration for the official
`@wonderwhy-er/desktop-commander` Remote Device, version **0.2.52**.
It is separate from the custom Chromium inspection worker in PR #719.

The image installs Node 22, Python, git, SSH client, system ripgrep and Chromium. npm lifecycle
scripts and Puppeteer's browser download are disabled; the package's documented
`remote --help` is executed during the build to verify the installed CLI.
The package and transitive npm dependencies are locked and installed with `npm ci`.
The lock is generated from npm for the exact CLI version. Security overrides pin
sharp 0.35.4 and ExcelJS's UUID 11.1.1; the image build exercises PNG decoding and
an XLSX write/read round trip to verify these dependency APIs remain usable.
The base image and Debian packages still float. Record the deployed image ID.
RDC and headless Chromium run together in this one container. The persistent
browser profile lives in `state/.config/chromium`; it is private session data,
not a CI artifact. The browser's control endpoint listens only on
`127.0.0.1:9222` inside the container; no host port is published. This does not
add a graphical desktop, automatic site login, or register an Alice browser adapter.

Chromium starts before RDC, with a 30-second readiness budget. Startup fails
if Chromium cannot run with its namespace sandbox; no `--no-sandbox` fallback
is used. A browser/RDC exit stops the other process so Compose can restart the
whole session. Normal shutdown allows five seconds to flush the browser profile.
`remote --help` does not start either a browser session or OAuth pairing.

The vendored `chromium-seccomp.json` derives from Microsoft Playwright v1.62.0
`utils/docker/seccomp_profile.json` (Git blob `fddc05fb520affb145404e6f6f647ca96af8087d`),
under the adjacent Apache-2.0 license. It is a Docker syscall profile, not a
Playwright dependency. It permits user-namespace creation. Local additions allow
`chroot` without granting a host capability, and return ENOSYS for `clone3` so
glibc uses `clone`. All Linux capabilities remain dropped; Docker's default-deny
syscall policy is preserved. Hosts must support unprivileged user namespaces.
The container has its own 256 MiB shared-memory allocation, a 2 GiB memory ceiling
and a 256-process limit; host IPC and privileged mode are not used.

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
`preflight`, then `install`, then `status`. The repository owner can also post an
exact `/rdc preflight`, `/rdc install` or `/rdc status` comment on canonical issue
#409. This route checks the OWNER association and repository-owner login, accepts
only these three complete comments, and checks out the event's exact master SHA;
PR code and other users' comments cannot reach the deployment job. It uses the existing production
`PREVIEW_SSH_*` secrets with strict host-key checking. Installation changes only
this service and its dedicated directories; it does not run preview/production
scripts or restart Traefik. Repeating installation keeps state and workspace.
CI builds the real image and renders a synthetic page twice with the same profile
volume, without starting RDC's OAuth flow. Installation checks browser readiness
on the actual host before reporting success and restores the previous image and
Compose configuration if that check fails. This proves Node and Chromium can run,
not that the device is paired or online.
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
# Protected server pairing redirect

## Cloud.ru compatibility probe

The registry API contract is taken from the checksum-verified official
[Terraform provider v2.1.3](https://github.com/cloud-ru/evo-terraform/releases/tag/v2.1.3)
(`linux_amd64` SHA256 `41b14bbf195131364d58d3f5d33face1d7f151d6b4ca6175bf6b0f6b83ede5a7`).
Its embedded protobuf descriptors use `/v1/registries` with `projectId`,
`registries`/`nextPageToken` pagination, and an asynchronous creation operation.
The probe accepts omitted or null empty collections and omitted private/Docker
defaults according to [ProtoJSON](https://protobuf.dev/programming-guides/json/),
rejects unknown response envelopes, and waits for the specific operation and
registry to become ready before image push. Registry reads and creation readiness
have separate 30-second budgets. The project-scoped route in the older public MCP
example returned HTTP 404 against the live service.

Probe health and cleanup ownership use the complete project container inventory,
matching the resource UUID, name, description and digest. This uses the list route
already verified in the live project; incomplete ownership data stops cleanup with
an explicit error. The stop operation remains the name-based v2 action documented
by the [Container Apps client](https://github.com/Nick1994209/cloudru-containerapps-mcp/blob/1c5fab2028f13991c52338fee6c1ae9ad719073f/internal/application/cloudru/containerapps.go).

The manual **Cloud.ru browser compatibility probe** workflow runs only from
protected `master` using the existing production IAM pair. It exports that exact
commit, builds the RDC image, pushes it to the dedicated private `alice-rdc-probe`
registry, and creates a separate `rdc-<12-hex-sha>` Container App pinned by
digest. This creates billable registry/image storage and brief container usage.
The probe uses scale 0–1 and requests a stop in cleanup; it does not delete the
image or container. A failed stop is a failure, and a stop request alone is not
confirmation that the provider has completed it. Inspect status after the run.
An existing same-name container is never taken over automatically.

Probe names are 16 characters. The live API rejected the earlier 30-character
`rdc-browser-probe-<sha>` name with HTTP 400 and a `name` validation violation;
the compact prefix preserves the same 12-character source identity.

The workflow's `name_preflight` action checks the complete inventory and a fixed
set of candidate names through `POST /v2/containers:check_name`. It builds no
image and creates or stops no resource. Availability is reported separately from
validation; it is not proof that a complete create request will be accepted.
Name-validation hints contain only fixed categories and explicit character-count
bounds; provider descriptions and regular expressions are never printed.
The endpoint and full inventory schema are documented in the current official
[OpenAPI](https://cloud.ru/docs/api/specs/container-apps-evolution/ug/_specs/openapi.yaml).

`ALICE_RDC_MODE=cloud-probe` starts only sandboxed Chromium and the synthetic
rendering check. It never starts RDC, pairing or an authenticated browser session.
The only HTTP route is `GET /healthz` on `0.0.0.0:$PORT` (default 8080); it returns
503 until rendering succeeds and then static readiness booleans. CDP stays on
loopback. Browser startup and live verification have separate 30-second budgets;
building/pushing the image is not part of those budgets.

This probe does not establish persistent-session support. Container Apps permanent
volumes use Object Storage and disallow socket/symlink operations needed by a live
Chromium profile. The probe uses disposable container storage. The existing
Compose deployment continues to use its local persistent volume and private CDP.
Full RDC deployment still needs a verified storage strategy and sandbox support;
there is no privileged or `--no-sandbox` fallback.

References: [volumes](https://cloud.ru/docs/container-apps-evolution/ug/topics/concepts__volumes)
and [unsupported volume operations](https://cloud.ru/docs/container-apps-evolution/ug/topics/troubleshooting__bucket-size-exceeded).

## Existing server redirect

The Commander startup preload observes the official `/device/start` response
without changing RDC's PKCE or polling. Only `verification_uri_complete` and a
maximum ten-minute expiry are written to `pairing/handoff.json`. This separate
directory is mounted read-only into production Alice; RDC's credential directory
is **not** mounted into Alice. The handoff is removed after authorization or a
terminal provider error and cleared on process startup.

After both deployments use this revision, visit `/<short-token>/rdc` while the
server is waiting for authorization. Alice returns a 303 redirect to the exact
server-generated verification URL. Complete the provider's account confirmation
there. An expired or absent handoff returns 503; unauthenticated access is denied.
No new OAuth callback or startup token flag is introduced.

Deployment order: deploy production Alice with this revision, then install RDC
with this revision using the existing protected deployment workflows. This code
does not authorize the device automatically: the account owner must confirm it.
