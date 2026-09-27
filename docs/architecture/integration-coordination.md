# Architecture integration coordination

This document records the merge order and ownership boundaries for the active
Alice Pro architecture workstreams. Issue
[#350](https://github.com/maksimp6/Chat/issues/350) is authoritative for
runtime and deployment architecture; issue
[#343](https://github.com/maksimp6/Chat/issues/343) remains the umbrella
execution plan.

## Non-negotiable boundaries

- Alice Pro runs preview environments in one Python process as managed worker
  threads. Threads are execution units, not security boundaries.
- `EnvironmentManager` owns environment creation, start, stop, restart, and
  cleanup. `RuntimeDispatcher` owns scoped resource access and cross-runtime
  rejection.
- Revision loading must not mutate process-global `sys.path`, environment
  variables, `sys.modules`, or shared import state.
- Preview traffic must not use a per-preview Flask process, Docker container,
  localhost proxy, or `runtime_port` transport.
- Tool and MCP calls converge on `UniversalToolExecutor` and preserve
  `InvocationContext`, `ExecutionTrace`, owner, and runtime correlation.
- Plugins, storage providers, conversation agents, and coding-agent backends
  consume these boundaries; they must not introduce parallel execution,
  authorization, tracing, or resource layers.

## Required merge order

The current dependency chain is:

1. **#348 — strict pytest configuration.** Merge first after review because its
   green checks unblock trustworthy application-test results for the runtime
   pull requests.
2. **#347 — gateway authorization and lifecycle.** Preserve a non-enumerating
   `404` for missing or cross-owner environments, a `503` for an owned but
   inactive environment, and deterministic worker/dispatcher cleanup after
   startup failure.
3. **#349 — branch-aware `RuntimeLoader`.** Rebase after #347. Accept only the
   constrained revision contract, prove simultaneous revisions do not collide,
   and state explicitly that in-process Python is not a sandbox.
4. **Revision HTTP/application contract.** Add a narrow request/response
   protocol on the existing worker and dispatcher path, including bounded
   streaming, cancellation, and runtime-local URL state.
5. **#341 — rendered preview smoke check.** Rebase and replace archive/SSH or
   per-preview deployment with host API calls that load the exact commit,
   verify the rendered shell and a streamed response, and always stop/delete
   the managed environment.
6. **Remaining runtime isolation slices.** Land filesystem, tools/MCP,
   conversation/configuration, and event/trace namespaces as separate pull
   requests, in that order.

No preview/runtime pull request that depends on revision loading should merge
before #347 and #349 are green together on their rebased heads. No step in this
sequence should be auto-merged.

## Status snapshot (2026-09-26)

| Pull request | Check state | Integration action |
| --- | --- | --- |
| #348 | Required checks green | Review, then merge before the runtime chain. |
| #347 | Application tests failing | Fix lifecycle response/cleanup behavior; rerun all checks. |
| #349 | Application tests/coverage gate failing | Rebase after #347 and close loader safety/coverage gaps. |
| #341 | Application tests failing; based before #342–#345 | Redesign after the revision HTTP contract; do not repair the old deployment path. |
| #336 | Older frontend base; backend check failing | Rebase after the immediate runtime/preview chain. |
| #331 | Existing checks green on an older base | Rebase and validate independently after the immediate chain. |
| #319–#322 | Stale dependency-only branches | Keep separate and rebase individually after architecture stabilizes. |

This table is a coordination snapshot, not a substitute for checking the live
head SHA and required checks immediately before review or merge.

## Parallel workstream ownership

Parallel work may proceed only when it consumes, rather than redefines, the
shared boundaries:

- **#227/#223 (frontend):** own progressive enhancement, initial rendering,
  BrowserShim/VM failure coverage, event lifecycle, and measured critical asset
  size. They do not own preview transport or runtime lifecycle.
- **#326/#238 (MCP/control plane):** own protocol compatibility, truthful
  metadata, authentication, and approval surfaces. Execution remains behind
  `UniversalToolExecutor` and runtime resources remain dispatcher-scoped.
- **#254 (plugins):** own manifest, persistence, permissions, and lifecycle.
  Plugin permissions map to existing capabilities; in-process plugins are not
  described as sandboxes.
- **#256 (autonomous development):** starts after plugin capability enforcement
  and uses a provider-neutral coding-agent backend. It may prepare branches and
  pull requests but never auto-merges.
- **#340 (cloud storage):** owns a provider-neutral storage contract with Google
  Drive as an adapter. Authorization, retries, and traces use the shared layers.
- **#116 (conversation agents):** extends the existing conversation identity and
  state model after conversation/config isolation; it does not create a second
  agent execution system.
- **#195/#104 (Android/release):** preserve separate debug/release signing and
  keep release secrets outside Git. Public release follows architecture
  stabilization.

When two pull requests touch the same boundary, the later workstream must rebase
and use the accepted contract rather than merge an alternative implementation.

## Readiness and rebase gates

Before a pull request in the dependency chain is marked ready:

1. Rebase it on current `master` after its direct dependency merges.
2. Confirm it contains no per-preview process/container, localhost proxy,
   process-global preview path, or direct runtime resource access.
3. Add behavioral coverage for authorization, failure rollback, cancellation,
   cleanup, and two-runtime isolation where applicable.
4. Run the runtime architecture validator, focused tests, full backend suite,
   formatting check, and required CI without lowering coverage gates.
5. Run SQLite and PostgreSQL paths for schema changes and the prescribed Android
   checks for Android changes.
6. Record migration, compatibility, security, and operational effects in the
   pull request. Do not auto-merge.

## Superseded assumptions

The following designs must not return through a follow-up or conflict
resolution:

- one container or Flask subprocess per preview;
- localhost or `runtime_port` routing between the gateway and a runtime;
- per-runtime mutation of `ALICE_PREVIEW_BASE_PATH` or other process globals;
- arbitrary branch imports through global `sys.path`/`sys.modules` changes;
- threads presented as protection from hostile Python code;
- direct runtime access to database, filesystem, network, process, MCP, tools,
  conversations, events, or traces;
- separate MCP, plugin, conversation-agent, or autonomous-agent execution
  universes;
- GitLab Duo as the mandatory coding-agent backend.

Useful requirements from older proposals should be moved behind the accepted
host, dispatcher, executor, and trace contracts rather than preserving their
obsolete deployment mechanisms.
