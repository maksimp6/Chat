# PR #1621: actual Redmi 9 runner regressions

These are offline code regressions executed inside GitHub Actions on the existing
physical Redmi 9 runner. They do not deploy the PR or prove live chat, model,
microphone, camera, browser-control or printing E2E.

## First run: failure preserved

- [Run 40381634312 / job 121779113804](https://github.com/maksimp6/Chat/actions/runs/40381634312/job/121779113804)
- Source: `450be1acf9bd1a386771dd5c356d92147297e93f`.
- Runner: `redmi9-python`; physical model checked with Android `getprop`.
- Result: **FAIL**, 154 Python tests passed and 15 failed; no test skips/errors.
- Both JavaScript suites passed. The separate expected-failure control passed:
  pytest returned 1, JUnit recorded one intentional assertion failure, and the
  external-network guards were loaded before that assertion.
- [Structured report](run-40381634312.json) preserves group counts, real exit
  codes, durations, log hashes, source SHA and job workspace paths.

| Group | Tests | Failures | Result |
| --- | ---: | ---: | --- |
| Core, files, runner, browser safety, G-code | 69 | 4 | FAIL |
| Credentials | 36 | 10 | FAIL |
| Messages, images, voice | 32 | 0 | PASS |
| Agent result delivery | 16 | 0 | PASS |
| Environment lifecycle | 16 | 1 | FAIL |
| JavaScript emulator and voice | 2 suites | 0 | PASS |

## Second run: 165 passed, four unresolved

- [Run 40382706143 / job 121782881638](https://github.com/maksimp6/Chat/actions/runs/40382706143/job/121782881638)
- Source: `75f860f5d6dff02fb58a8573797ae7d6db934ee7`.
- **FAIL**: 165 Python tests passed, four failed; both JavaScript suites and
  the separate expected-failure control passed. No test skips/errors.
- [Structured report](run-40382706143.json).
- Credential and lifecycle failures were corrected and verified by this run.
- Android refused native hard-link creation with `Permission denied` in the two
  ZIP fixtures. These are an actual platform/access blocker. No privilege,
  security-setting, filesystem or remote-access change is used to force a pass.
- Git LFS pull returned zero, but the two STL-readiness cases still saw incorrect
  source hashes. That exit status did not establish hydration of the asset bytes.

The follow-up changes the STL download method. It does not repeat the denied
hard-link calls, remove them from the full suite or turn their failures into PASS.

## Focused STL follow-up: two cases passed

- [Run 40383967914 / job 121787212366](https://github.com/maksimp6/Chat/actions/runs/40383967914/job/121787212366)
- Source: `2afa92965668cc0b9f7c36457b352d6785e9d7b2`.
- Focus: `stl_readiness`, exactly two previously failing STL-readiness cases.
- **PASS within that scope**: two tests passed, no failures/errors/skips; the
  separate expected-failure control also passed.
- All 14 committed STL objects were verified by byte length and SHA-256 before
  the tests. Total: 659,676 bytes. The manifest and expected hashes were not changed.
- [Structured report and per-file asset proof](run-40383967914.json).
- [Actions screenshot](stl-run-40383967914.jpg) confirms only this focused run;
  its green status must not be used as a full-suite or live-application PASS.

## Combined disposition: BLOCKED, not a full-suite PASS

Across the full second run and the focused follow-up, **167 of 169 distinct
Python tests passed**, plus both JavaScript suites. This combines two explicit
scopes; there is no single green full-suite run. Application source was unchanged
between these two revisions; only validation setup, scope and evidence changed.
The two hard-link cases remain **BLOCKED by Android's permission denial**, with
their actual failed attempts preserved in both full-run reports. No test is
removed, skipped or relabelled PASS to hide that limitation. Draft remains
appropriate; these results do not authorize merge or establish live E2E.

## Corrections after the first run

1. Android's Python build omits `os.link`; the ZIP fixtures now use the already
   installed native `ln` when the Python API is unsupported. They require a real
   shared inode and link count, never a copy, symlink, mocked inode or skip.
2. The isolated checkout originally left STL LFS pointers in place. The initial
   Git LFS pull attempt did not resolve the hash failures. The follow-up downloads
   only the 14 required STL objects from the exact public commit, verifies the
   pointer OID and manifest byte count/SHA-256, and publishes only matching bytes.
   The fixtures total 659,676 bytes; unrelated LFS data remains untouched.
   Production mesh hash/geometry checks remain intact.
3. Credential fixtures still expected the removed admin-token gate or patched
   its deleted `_guard`. They now exercise the current shared-installation
   contract, including real persistence of synthetic credentials and provider
   rejection independently of the old admin token. No production authorization
   or privacy rule was weakened to satisfy the fixtures.
4. One credential fake lacked the store's `get` interface used by the previous
   credential lookup. The error-log regression now expects the error type and
   explicitly rejects internal exception text in both log and response.
5. The HTTP timeout fixture patched an obsolete request queue and never
   registered a runtime. It now runs a registered runtime and an Event-blocked
   HTTP handler; the invalid-start negative path uses the current HTTP worker seam.

## Isolation and remaining limits

The workflow uses a new source checkout pinned to `GITHUB_SHA` and private
per-test memory, image, environment and log directories. No provider credentials
are inherited by test children; synthetic encryption material is used. Python
and Node network guards prevent the selected suites from making external calls.
Existing Python, Node and native dependencies are reused; none are installed.
The live server, device access, runner lifecycle and production data are not
changed. No age/count cleanup is added, and the first failing evidence is retained.

Empty suites, timeout, missing dependencies, test skips/errors and a failed
negative control are not PASS. The intentional negative-control failure is
recorded separately from product test counts. Passing these regressions cannot
establish installed-runner compatibility, long-outage result durability, image
backup/retention, all G-code compatibility or readiness to show the live app.

At the focused run's end the job filesystem reported 114,442,240 free bytes.
This is that timestamped runner observation, not a guarantee of current free
space or a storage/retention fix. Test evidence and earlier workspaces were retained.
