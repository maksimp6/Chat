# Current-scope integration staging

This document is the ledger for the staging branch tracked by issue
[#351](https://github.com/maksimp6/Chat/issues/351). The staging pull request is
not a replacement for focused feature pull requests and must never be merged
automatically.

The runtime and deployment decisions in
[#350](https://github.com/maksimp6/Chat/issues/350) are authoritative. Issue
[#343](https://github.com/maksimp6/Chat/issues/343) remains the execution
coordinator.

## Integration rules

A focused pull request is eligible for this branch only when it:

1. is based on the expected architecture and current dependency chain;
2. has completed required review and has green required checks;
3. preserves the one-process, managed-thread `RuntimeDispatcher` and
   `RuntimeLoader` design;
4. does not introduce a per-preview Flask process, Docker container,
   localhost proxy, `runtime_port` transport, or process-global preview state;
5. keeps tool execution behind `UniversalToolExecutor` and runtime resources
   behind `RuntimeDispatcher`; and
6. remains independently reviewable on its own branch.

Failures found in combined testing should be fixed in the owning focused pull
request where practical, then re-integrated. Integration-only conflict
resolutions must be documented here and must follow #350 rather than make
incompatible implementations merge mechanically.

## Dependency order

Integrate candidates in this order:

1. repository-wide test infrastructure required by the runtime chain;
2. #347, centralized runtime owner policy;
3. #349, branch-aware `RuntimeLoader`, rebased on #347;
4. the successor to #341, rewritten for the host-managed preview lifecycle;
5. runtime filesystem, tools/MCP, conversation/configuration, event/trace, and
   revision HTTP/streaming isolation slices in their documented dependency
   order; and
6. independent frontend, MCP, plugin, cloud storage, conversation-agent, and
   Android/release slices only after their own prerequisites are satisfied.

Dependency-only pull requests #319-#322 stay separate and are rebased and
validated individually after the immediate runtime/preview chain stabilizes.

## Initial candidate ledger

Status recorded on 2026-09-26 against `master` commit `07fdd0d`:

| Candidate | CI status | Integration status | Required next action |
| --- | --- | --- | --- |
| #348 strict pytest configuration | Required checks green; no review recorded | Blocked | Complete review before staging. |
| #347 runtime owner policy | Application tests failing | Blocked | Fix lifecycle response and cleanup behavior on #347, then rerun all checks. |
| #349 branch-aware loader | Application/coverage job failing; depends on #347 | Blocked | Resolve loader safety findings, restore 100% changed-line coverage, and rebase on safe #347. |
| #341 rendered-shell smoke check | Application tests failing and based on obsolete deployment assumptions | Replace, do not stage | Preserve the rendered-shell assertion in a host-managed lifecycle successor after #347/#349 and the revision HTTP contract. |

No focused feature commit is included in the initial staging branch because no
candidate has both completed review and satisfied its required checks. This
empty-candidate state is intentional: establishing the integration PR must not
bypass the admission policy.

## Combined validation

After every admitted candidate, record its pull request and exact commit in
this ledger and run the relevant focused checks followed by:

```console
python -m compileall -q .
python tests/validate_runtime_modules.py
pytest -q
bash scripts/format.sh check
git diff --check
```

Run the prescribed Android unit-test and debug-assembly command when an
admitted change can affect Android or shared release behavior. Schema changes
must validate both SQLite and PostgreSQL paths. Security-sensitive changes must
include authorization, isolation, cleanup, and secret-redaction regression
coverage.
