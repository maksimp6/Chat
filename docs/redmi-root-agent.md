# Redmi 9 native RootAgent CLI

The native CLI lets a genuine GitHub Actions job on the rooted Redmi 9 inspect
root access, capture an approved screen, and submit relative mouse input through
the existing production C daemon. It uses the runner's Python and `su`.

The legacy Android RootAgent APK and its matching instrumentation package remain
absent. This CLI does not restore that Android service. It requires no APK build,
Gradle invocation, paid API, new credential, or replacement input daemon.

## Execution boundary

Run these commands only inside an actual GitHub Actions job on the authorized
Redmi 9 runner. Keep the job's real `GITHUB_ACTIONS`, `CI`, `GITHUB_RUN_ID`,
`GITHUB_SHA`, `RUNNER_NAME`, and `RUNNER_TEMP` context. Do not manufacture that
context to run locally. `RUNNER_TEMP` must identify a real job temporary directory.
The private `_worker` entry point is internal to the CLI's `su` invocation.

The device guard requires model `Redmi 9`, device `galahad`, and a root worker.
Capture and input fail closed when Keyguard or foreground state is unknown.
They also require Android's early/fully-on display flags; an unlocked Keyguard
alone does not establish that the display is awake.
The CLI does not unlock the phone, enter a PIN, restart RDC, install software,
restart the mouse daemon, or change its boot service.

## Existing input backend

| Check | Required value |
| --- | --- |
| Production binary | `/data/adb/alice-mouse/mouse` |
| Unix socket | `/data/adb/alice-mouse/control.sock` |
| Parent directory | Root-owned directory, mode `0700` |
| Socket | Root-owned socket, mode `0600` |
| Connected peer | `SO_PEERCRED` UID `0`, valid PID |
| Peer executable | SHA-256 `9d25ff623eb29fc9b9f72e9e2126d52144db2181508229aac91eb4cd0de38516` |

The executable is checked through the connected peer's `/proc/<pid>/exe`.
A matching pathname alone is insufficient. A failed check stops the operation;
it does not trigger installation, restart, or fallback to another input channel.

The daemon accepts relative `move dx dy` and `click dx dy` commands. Each delta
is an integer from `-500` through `500`. `click` first applies its relative
movement and then presses/releases the left mouse button. `click 0 0` clicks at
the current cursor position. These are not absolute screen coordinates.

The inspected device's `dumpsys input` exposed the production Cursor Input
Mapper but no usable current pointer coordinates. Screen dimensions do not
establish the pointer's position.

## Commands

Invoke `scripts/redmi_root_agent.py` with the runner's Python from an actual
Actions step. The public client remains unprivileged and invokes its bounded root
worker internally.

| Command | Meaning |
| --- | --- |
| `status` | Sanitized device, root, Keyguard, focus-presence, and socket metadata; no input |
| `probe` | Connect to the existing socket and verify root peer/executable hash; send no input command |
| `wake` | If display-on is unconfirmed, submit one fixed relative `move 1 1`, without clicking; verify actual wake separately |
| `capture --output PATH [--expected-package PACKAGE]` | Capture and validate PNG; require stable native focus/window identity before and after capture |
| `move DX DY --expected-package PACKAGE` | Submit bounded relative movement after unlocked/foreground checks |
| `click DX DY --expected-package PACKAGE` | Submit one bounded relative movement and left click after unlocked/foreground checks |

The E2E always supplies `--expected-package` for capture and input. A package
check alone does not establish that the browser is displaying the owned test
page; the acceptance checks below provide that additional evidence.

PNG validation checks the signature, bounded dimensions and length, chunk
ordering and CRCs, supported pixel profile, and exact decompressed scanline
length/filter values. The client creates a new output file with mode `0600`,
without following a final-path symlink or overwriting an existing file. Public
stdout contains JSON metadata and a file path, not PNG bytes or UI text. The
worker's PNG transfer is private subprocess IPC and must not be copied into logs.

A daemon `OK` acknowledgement produces `submitted: true`, `verified: false`,
and `coordinates: "relative"`. It proves acceptance by the daemon, not a visible
Android result. An uncertain delivery or acknowledgement must not be retried
automatically: the click may already have happened.

## Acceptance on an owned local page

`scripts/redmi_root_agent_e2e.py` uses a disposable neutral page served only on
`127.0.0.1` at an ephemeral port. Its button starts red with counter `0` and turns
green with counter `1` after one accepted trusted click. The temporary HTTP
server exists only for this test and is closed afterward; production input
continues through the existing root-only Unix socket.

The E2E tries to wake the display through the verified existing mouse peer with
one fixed relative `move 1 1`, without a click. It repeats the display check
before capture and input, sending no wake event while the display is on. A
daemon ACK alone does not confirm wake. It never dismisses
Keyguard, enters a PIN, or changes the screen timeout. It waits for display-on
flags, a focused native window, a nonzero browser viewport and painted frames.

Acceptance requires all of the following in the same real Actions job:

1. Confirm device, root, unlocked state, and the pinned production mouse peer.
   Opening the local test page must not require unlocking the phone or handling
   a browser permission, welcome, or login dialog.
2. Observe a fresh heartbeat from the owned page reporting visible, focused
   state. Confirm the expected browser foreground and native focused window.
3. Run the programmatic `button.click()` negative control. Its untrusted event
   must be ignored: the accepted-click count remains `0`, and the button stays
   red. An HTTP field claiming `trusted: true` is not independent evidence;
   witness messages must belong to this local page and this run.
4. Capture the initial frame with the expected package and unchanged native
   focused window before/after capture. Require the fresh owned-page heartbeat
   and the expected red test-page pixels before retaining the test frame.
   A stale ready event or arbitrary browser screenshot is insufficient.
5. Only after those page/foreground/capture checks, allow bounded hover-only
   relative homing. Homing sends no clicks and does not claim absolute pointer
   coordinates. Maintain the unlocked and expected-browser checks for every
   movement.
6. After the final movement, observe a new trusted pointer event inside the
   test button. Gate the click on a fresh event serial, current `inside=true`,
   and no intervening pointer leave. An earlier hover event cannot authorize
   the click. Submit one stationary `click 0 0` while those conditions hold.
7. Require exactly one trusted accepted click, a completed paint, and a fresh
   visible/focused heartbeat. Capture again with the expected package and
   stable native window, validate the expected green test-page pixels and
   counter transition, and keep only the owned-page evidence.

Bounded hover homing has the page, foreground, and initial-frame preconditions.
A click additionally requires fresh proof that the pointer is currently inside
the owned button. Unknown pointer state, stale focus, a native dialog, unexpected
page pixels, or ambiguous delivery stops the acceptance attempt.

Do not capture public sites, another browser tab, messages, account pages, or
system dialogs as acceptance artifacts. Reports should contain sanitized job
identity, source SHA, backend/probe metadata, negative-control outcome, trusted
click count, and validated owned-page frame metadata.

## Evidence status

The [native acceptance job](https://github.com/maksimp6/Chat/actions/runs/40192174130/job/121165309069)
passed on 25 October 2026 at 12:14 UTC, on runner `redmi9-python` (ID 21).
The tested source SHA was `173d1509a6effab10a66fcf890e6d7c3f09ca8aa`;
the adapter implementation was published in `8bf849b65d42d4fc3ce74d8f93e4a95cfc2c7614`.
The intervening commit changed only the application's `static/style.css`.

| Evidence | Actual result |
| --- | --- |
| Unit controls, executed on the phone inside Actions | 35 passed |
| Device / root | Redmi 9 / galahad / UID 0 |
| Existing C mouse peer and executable hash | Verified |
| Programmatic negative control | Untrusted click ignored; accepted counter remained 0 |
| Physical input | Exactly 1 trusted click via the existing C mouse |
| Native frame | 1080 × 2340; red counter 0 changed to green counter 1 |
| Before PNG SHA-256 | `65ccaa07e683a66a1b3187dbf054dfd80aa73d270fbaa793b4bd3c9750eacab8` |
| After PNG SHA-256 | `baa94bddf1d428aa75a145738df7dc725f59238cc6ccae8509280f26d9ba3585` |
| Overall E2E | `PASS_TRUSTED_BROWSER_EVENT_AND_CHANGED_PNG` |

The display was already on in this successful run: `wake` returned
`already_awake: true`, `input_operations: NOT_RUN`. Waking an asleep display
with the fixed mouse event therefore remains unverified on hardware; its
request/ACK/no-retry controls were unit-tested in the runner. The CLI must stop
if the display-on check fails.

`status`, a successful `probe`, a daemon ACK, or a valid PNG alone do not prove
visible input. The successful result above depends on the trusted event,
exact counter transition, and independently validated native green frame.
PNG files were retained in the successful phone job workspace; this native
runner does not upload standard Actions artifacts. Frame hashes and the full
sanitized acceptance report are in the linked job log.

## Logos tool

Logos reaches the RootAgent through two tools, `rootagent_run` and
`rootagent_result` (category `rootagent`, source `tool_providers/rootagent.py`).
`rootagent_run` dispatches the `rootagent_command` action of
`.github/workflows/redmi9-app-server.yml` on `develop` and returns the run id at
once; it never waits for the device. `rootagent_result` reads that run's status
and, after the run completes, its JSON result. The device work happens only in
that GitHub Actions job, on the existing `redmi9` runner.

### What is configured, and by whom

| Setting | Where it lives | Set by | Required value |
| --- | --- | --- | --- |
| Environment `rootagent-command` | GitHub repository settings, Environments | Repository owner | Required reviewers. GitHub creates the environment on first use with no reviewers, so configure them before the first dispatch. The job declares `environment: rootagent-command`. |
| Token | Logos server environment variable `LOGOS_ROOTAGENT_DISPATCH_TOKEN` | Server operator | Fine-grained token with Actions read and write on this repository only |
| Repository | Logos server environment variable `LOGOS_ROOTAGENT_REPOSITORY` | Server operator | `owner/name` of this repository |

The token is never written to the repository, to logs, to tool results or to
error text. If either variable is missing, the tool returns an explicit error
and does not call GitHub.

### What the job allows

- The job runs only for `refs/heads/develop`. Other branches never start it.
- Commands are `status`, `probe`, `capture`, `move` and `click`. There is no
  `wake`, no text or key input, no unlock, no PIN, and no retry of an uncertain
  input.
- `status` and `probe` take no arguments. `capture` takes a package name.
  `move` and `click` take `DX DY PACKAGE`, with `DX` and `DY` integers from
  `-500` through `500`. The job validates the arguments before anything runs,
  and the tool applies the same rules.
- `capture`, `move` and `click` need the caller's approval, checked inside
  `rootagent_run`. `status`, `probe` and `rootagent_result` need none.
- The JSON result is uploaded as a workflow artifact, and for `capture` only,
  the PNG too. Both have a retention of 1 day and nothing is committed. The PNG
  is written to the RAM disk, never to flash.
- The job has an 8-minute timeout.
- `rootagent_native_check` and the device guard are unchanged. This job is the
  only one in the workflow that uploads artifacts.

### Reading a dispatch

A successful `rootagent_run` means the job was accepted, not that the phone
acted. The CLI's own rules still apply: a daemon `OK` is `submitted`, not
`verified`. If GitHub accepts a dispatch but returns no run id, the tool says so
and the run must not be dispatched again; find it in Actions on `develop` first.
A dispatch that failed with no response is reported as unknown for the same reason.
