# Redmi 9 native RootAgent CLI

The native CLI lets a genuine GitHub Actions job on the owner's rooted Redmi 9
drive the phone as a user: inspect root access, capture the screen, tap, swipe,
type text and send keys through root `input`, and submit relative mouse input
through the existing production C daemon. It uses the runner's Python and `su`.

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
Absolute `tap`/`swipe` coordinates are bounded against the real screen size read
on the device. Input is no longer gated on Keyguard or foreground state: this is
the owner's own phone, driven as a user. The legacy relative `move`/`click` keep
using the existing pinned C daemon.

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

Logos drives the owner's own phone as a user. Absolute `tap`, `swipe`, `text`
and `key` go through root `/system/bin/input`, which injects trusted events
across the whole device; `capture` reads the screen; `move`/`click` keep the
legacy relative input through the existing root mouse daemon.

| Command | Meaning |
| --- | --- |
| `status` | Sanitized device, root, Keyguard, focus, screen size, and socket metadata; no input |
| `probe` | Connect to the existing socket and verify root peer/executable hash; send no input command |
| `ui` | Wake the display and return the screen as text: `ui_elements` from `uiautomator dump` with text, description, id, flags, bounds and center; no screenshot |
| `wake` | If display-on is unconfirmed, submit one fixed relative `move 1 1`, without clicking; verify actual wake separately |
| `capture --output PATH [--expected-package PACKAGE]` | Capture and validate the PNG, plus `ui_elements`; any expected package is reported, not enforced |
| `tap X Y` | Tap absolute screen coordinates through root `input tap` |
| `swipe X Y X2 Y2 [--duration-ms MS]` | Swipe/scroll from (X, Y) to (X2, Y2) through root `input swipe` |
| `text STR` | Type a string through root `input text` |
| `key KEYCODE` | Send a key (e.g. `KEYCODE_HOME`, `KEYCODE_BACK`, `KEYCODE_ENTER`, or a number) through root `input keyevent` |
| `browse URL --output PATH [--wait-ms MS]` | In one call: wake the display, dismiss a non-secure keyguard, open the URL with `am start -a VIEW`, wait, and capture the screen |
| `move DX DY [--expected-package PACKAGE]` | Submit bounded relative mouse movement through the C daemon |
| `click DX DY [--expected-package PACKAGE]` | Submit one bounded relative movement and left click through the C daemon |

Absolute coordinates are bounded against the real screen size read on the
device. `text` is passed to the CLI only through a validated request file
(`--request`), never through a shell command line or an environment variable,
and the string itself is never echoed into the result or the logs.

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

**On the phone (the normal deployment), `rootagent_run` runs the command directly
on the device and returns the result at once — no GitHub, no token.** It invokes
`scripts/redmi_root_agent.py --local-request <file>`, which hands the request to
the root worker; the worker's `device_guard` pins the Redmi 9 identity as root on
every command. For `capture`/`browse`/`batch` the screenshot comes back in the
`capture` field, so `rootagent_result` is not needed.

The model never receives the screenshot as text: a function output is a string, and
megabytes of base64 there could not be seen and would fill the context (#1708). The
chat tool loop (`chat/mcp_routes.py::_take_screenshots`) replaces every `png_base64`
with a note and, for a model that reads images, attaches the PNG to the next request
as an `input_image`. For any other model the screenshot is described in text by
the first model that reads images (`qwen3.6-35b-a3b` today) and the description goes
into `screen_description` of the result; a failed description is returned there as
`error`.

**The screen as text.** `ui` (no screenshot, cheap) and every `capture`, `browse`
and `batch` also return `ui_elements` from `uiautomator dump`: each element with
`text`, `description`, `id`, `class`, `flags` (clickable, editable, checked,
scrollable, …), `bounds` and `center`. A model taps the `center` of the element it
needs instead of guessing coordinates. A failed dump (for example a secure screen)
comes back as `ui_error`; the screenshot still counts.

`text` (and a batch `text` step) types through a virtual US keyboard: printable ASCII
and tab only. Cyrillic and emoji are refused by the tool before the phone is called.
Tap and swipe coordinates run from 0 to width−1 and height−1 of the real screen.

The GitHub Actions path is a fallback for when Logos is **not** on the phone:
there `rootagent_run` dispatches the `rootagent_command` action of
`.github/workflows/redmi9-app-server.yml` on `develop`, returns a run id, and
`rootagent_result` reads the run's status and JSON result. That job runs on the
existing `redmi9` runner and only for `develop`.

### What is configured, and by whom

Direct on-device execution needs no configuration at all. The two environment
values below are only for the GitHub Actions fallback:
`LOGOS_ROOTAGENT_DISPATCH_TOKEN` (the GitHub token) and
`LOGOS_ROOTAGENT_REPOSITORY` (`owner/name`). The deploy provisions both onto the
phone automatically, so the fallback has one manual step only: the token.

| Setting | How it reaches the server | Manual step |
| --- | --- | --- |
| Token | Repository secret `ROOTAGENT_DISPATCH_TOKEN` → deploy writes it to `logos-local-state/rootagent-dispatch.token` (mode 0600) → launcher sets `LOGOS_ROOTAGENT_DISPATCH_TOKEN` | **Once:** add the repository secret (Settings → Secrets and variables → Actions). A phone-local token file is left untouched when the secret is unset. |
| Repository | The deploy writes `$GITHUB_REPOSITORY` to `logos-local-state/rootagent-repository` → launcher sets `LOGOS_ROOTAGENT_REPOSITORY` | None — derived from the deploy context. |

The token must be a fine-grained personal access token on **this repository
only**, with **Actions: read and write** and **Contents: read and write** (the
server dispatches the workflow and writes `pending/<run_id>.json` / reads
`runs/<run_id>.json` on the `rootagent-results` branch). A long-lived token is
unavoidable: the server outlives any Actions run, and a run's own
`GITHUB_TOKEN` expires with it.

The flow end to end: owner adds the repository secret once → the next push (or a
`deploy` dispatch) restarts the server with the token in its environment →
`rootagent_run` works with no further setup. The token is never written to the
repository, to logs, to tool results or to error text, and the runner masks it
in deploy output. If either value is still missing, the tool returns an explicit
error and does not call GitHub.

### What the job allows

This is the owner's own rooted phone, driven as a user. The job adds no approval
prompt of its own; its boundary is the branch and the real runner.

- The job runs only for `refs/heads/develop`. Other branches never start it.
- Commands are `status`, `probe`, `capture`, `tap`, `swipe`, `text`, `key`,
  `move` and `click`. `tap`/`swipe` use absolute coordinates, `text` types a
  string, `key` sends a keycode, and `move`/`click` keep the relative C-daemon
  input.
- `status` and `probe` take no arguments. `capture` takes an optional package.
  `tap` takes `X Y`; `swipe` takes `X Y X2 Y2 [DURATION_MS]`; `text` takes a
  one-line string (≤ 4096 chars); `key` takes a keycode. `move`/`click` take
  `DX DY [PACKAGE]` with deltas from `-500` through `500`. The job validates the
  arguments before anything runs, and the tool applies the same rules.
- The validator writes the whole request to a JSON file, and the CLI reads it
  with `--request`; no argument, least of all free text, passes through a shell
  command line or an environment variable.
- No command needs an in-tool approval: Logos dispatches directly.
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
