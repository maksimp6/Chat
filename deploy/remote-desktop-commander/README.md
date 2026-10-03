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

Probe names are 16 characters and preserve the 12-character source identity.
Container Apps sets `PORT` from `containerPort` and forbids overriding that
environment variable. The probe keeps `containerPort: 8080` and sends only
`ALICE_RDC_MODE` in its environment; the server already reads the platform port.
See the official [runtime contract](https://cloud.ru/docs/container-apps-evolution/ug/topics/concepts__runtime).
Health verification accepts HTTPS application hosts under the current
`*.containerapps.ru` domain and the older `*.containers.cloud.ru` domain, with no
redirects or IAM headers. The current domain is documented in the official
[deployment guide](https://cloud.ru/docs/tutorials-evolution/list/topics/container-apps__deploy-frontend-app).

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
The Cloud.ru probe on reviewed commit `fdd3de4` passed sandboxed Chromium and
synthetic rendering ([live run](https://github.com/maksimp6/Chat/actions/runs/37114179318)).
It created `rdc-fdd3de412cb7`, UUID `94ae3a86-671f-40ae-9323-e81d3626135e`,
and requested stop; a subsequent inventory confirmed one retained container.
This evidence covers disposable browser execution, not persistent authentication.
There is no privileged or `--no-sandbox` fallback.

References: [volumes](https://cloud.ru/docs/container-apps-evolution/ug/topics/concepts__volumes)
and [unsupported volume operations](https://cloud.ru/docs/container-apps-evolution/ug/topics/troubleshooting__bucket-size-exceeded).

## Permanent Cloud.ru RDC

The **Cloud.ru persistent Remote Desktop Commander** workflow operates one
dedicated service: `rdc-<first 12 project UUID hex digits>`. It uses 1 vCPU,
4 GiB, scale **1–1**, a private digest-pinned image, and disabled auto-deployment.
Hot instances remain active between HTTP requests and incur continuous compute
charges; see [scaling](https://cloud.ru/docs/container-apps-evolution/ug/topics/container__scaling).
It never takes over the separate compatibility-probe record or the Alice app.

Run `preflight`, then `install` from protected `master`. All lifecycle actions
require the
**Object Storage tenant ID from the same project**, supplied as the workflow
input `storage_tenant_id` or existing variable `CLOUDRU_STORAGE_TENANT_ID`.
The tenant ID is not a credential and cannot be replaced by the project ID.
Ownership checks compare any returned managed-mount tenant ID with this exact
configured tenant before operating on the service.
`status` reports the verified service name/UUID and a fixed provider state before
checking runtime health; this identity diagnostic does not claim RDC is ready.
If the colon `testCall` API returns transport HTTP 400 for `GET /healthz`, the
runner tries the documented slash route within the same request timeout. This
compatibility path is read-only: checkpoint always requires the colon route
and its nonce header. Reading health does not prove checkpoint support.
Cloud.ru may add a global volume `readOnly` attribute. Ownership accepts it only
when omitted or explicitly disabled (`false` as a boolean, or the exact strings
`false`, `False`, `FALSE`); empty, null, numeric, enabled and unknown values are
rejected. The mount must also remain writable. This preserves the documented
[volume access rules](https://cloud.ru/docs/container-apps-evolution/ug/topics/concepts__volumes).
Existing production IAM signs S3 operations only in the reviewed runner.
The application receives a managed `/rdc-state` bucket mount; IAM, S3 and SSH
keys are not passed to RDC. The dedicated bucket name is
`alice-rdc-state-<first 12 project UUID hex digits>`. Creation uses the documented
[S3 API](https://cloud.ru/docs/s3e/ug/topics/api__createbucket); private ACL and an
exact project/service ownership marker are verified before use. An existing
unmarked or non-private bucket is rejected.

Live Chromium, RDC configuration and `/workspace` stay on local POSIX storage.
Only closed regular snapshot files are written to Object Storage. Two slots,
generation numbers and checksums retain the previous complete checkpoint during
an incomplete write. Restore validates the whole archive, refuses path traversal,
links/devices and oversized data, and installs private local files before starting
Chromium or RDC. Workspace owner executable bits are retained; group/world access
and setuid bits are removed. Browser cache and `Singleton*` runtime files are
excluded. Workspace links are unsupported by this snapshot format.

Snapshots are limited to 128 MiB compressed, 256 MiB of regular file contents
and 20,000 archive entries. Restore stages validated content in a private directory
under the local home, keeping expanded files out of Compose's 256 MiB `/tmp`.
Exclusive bounded copies install files across separate local home/workspace
filesystems. If any copy fails, restore rolls back entries created by that attempt,
preserves preexisting entries, and blocks startup; a later attempt can retry.
The archive and content can temporarily occupy up to 640 MiB during copying,
plus authorization
data and filesystem/entry overhead. This is a data bound, not a guarantee of total
disk usage; insufficient local space blocks startup. The Object Storage mount
still receives only closed regular files and never rename or fsync operations.

Committed `device.json` updates, including rotated refresh tokens, are journaled
separately. Invalid or possibly newer corrupt authorization fails closed instead
of reverting to stale credentials. A persistence failure stops the runtime.
The `restart` operation first quiesces RDC and Chromium, verifies their process
groups have finished, saves closed state, requests provider stop, and confirms
`suspended` before starting another revision. `stop` performs the same checkpoint
and confirmed suspension. A completed checkpoint retains a verified quiesced
status and cached generation,
so an ambiguous provider stop can be retried without starting another writer.
Neither operation deletes the bucket, images or state.
`start` resumes only an exactly owned, confirmed suspended service and checks
restored runtime health and the authorization gate; it never creates another app.
Do not create rolling revisions or a second writer manually. Abrupt provider loss
can lose browser/workspace changes since the last completed checkpoint; SIGTERM
storage upload is best effort, because the provider does not publish a guaranteed
grace period.

Pairing is available at the reported `/rdc/pair` link behind Cloud.ru's native
project/organization-role authorization. Log in to Cloud.ru, then approve the
exact device in the official RDC account. The redirect accepts only the official
HTTPS hosts and expires within ten minutes. This trusted audience includes users
with project/organization access, not only the owner; see the official
[invocation guide](https://cloud.ru/docs/container-apps-evolution/ug/topics/guides__container-invoke).
Raw RDC output is discarded because upstream errors can include token arguments.
The reported protected `/rdc/pair` application link can appear in Actions logs.
The secret upstream verification URL/code, credentials and browser data never
reach Actions logs or artifacts. The workflow uses IAM `:testCall` only for
`/healthz` and `/checkpoint`,
and verifies anonymous ingress cannot retrieve health data. Checkpoint additionally
requires a fresh, short-lived permit written by the production runner to the private
state bucket. The HTTP request carries a random nonce; the bucket holds only its
hash, bound to the operation, project and container. A project/organization user
with only native-ingress access cannot authorize checkpoint; private bucket writers
and authorized RDC tools remain trusted. The nonce is excluded from logs, health
and snapshots. CDP remains loopback.

`status` reports safe readiness and a device UUID. `RDC_QUIESCED` confirms a
completed checkpoint awaiting provider suspension; retry `stop` to complete it.
`RDC_RUNNING` means the local
session has been saved; complete connectivity still requires the same device to
appear **Online** in the connected RDC plugin and an explicit-device tool call
to succeed. Repeat this check after the controlled `restart` before reporting
durable connectivity. Account confirmation cannot be performed automatically.

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
