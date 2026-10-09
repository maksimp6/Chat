# Alice Mouse test acceleration — Issue #650

This is an **incomplete staging slice**. The parallel runner and C fixture are
committed here for review, but the 12 Alice Mouse test modules and supporting
runtime modules are currently only available on the Redmi 9. The runner must
**not** be added as a required CI check until those dependencies are ported.

## Observed Redmi 9 results

- Existing sequential 52-test wall median: 8.121 s (three runs).
- Parallel five-group runner: 52/52 tests pass; outer wall median 5.106 s
  (five subprocess-wrapped runs), internal wall median 4.745 s.
- Direct shell timing of `python alice_mouse_parallel_tests.py`: 4.708 s,
  4.680 s, 5.396 s; therefore **not reliably below five seconds**.
- Device remained online: one Alice virtual mouse, Mouse Daemon PID 30813,
  Live Server PID 4995, no leftover synthetic test children.

These numbers are measurements from a local prototype, **not CI results for
this branch**. CI timing and p95 require a larger sample and exact-head runs.

## Safety and prerequisites

1. Port the 12 test modules, the production-independent support modules, and
   the C fixture compilation step from Redmi 9 into a reviewed, reproducible
   test tree. Avoid embedding device secrets, tokens, or private paths.
2. Preserve real timeout, SIGTERM, crash, rollback and recovery tests. Do not
   replace every real timing test with mocks merely to satisfy a duration target.
3. Ensure separate workers have isolated temporary directories, sockets,
   ephemeral ports, and process trees. Count all 52 tests and fail on any worker
   error, missing summary, or leaked child.
4. Run A/B on the same runner/device with multiple repetitions and report
   full command wall-clock median and p95. A five-second budget is a target,
   not a reason to remove coverage.
5. Never run these tests against live Magisk or `/dev/uinput`. Keep production
   RDC and Mouse Daemon untouched.

**Status:** Draft / BLOCKED until dependencies, CI and independent review pass.
No merge authorization.
