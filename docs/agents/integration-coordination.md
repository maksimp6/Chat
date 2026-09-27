# Architecture integration coordination

This document is the merge-time coordination checklist for issue #343. It is a
status snapshot, not a replacement for the authoritative runtime architecture in
issue #350 or the repository runtime policies.

Status checked: 2026-09-26.

## Authority and invariants

Review runtime, preview, MCP, plugin, storage, agent, and deployment changes
against issue #350 and these non-negotiable boundaries:

- Alice Pro is one host Python process. Environments are managed execution
  contexts and worker threads, not per-preview containers or Flask processes.
- `EnvironmentManager` owns environment lifecycle; `RuntimeDispatcher` owns
  routing, authorization, scoped resources, execution, and tracing.
- Threads are execution units, not security boundaries. Runtime code must not
  mutate process-global imports, environment, configuration, or other shared
  state.
- Tool calls converge on `UniversalToolExecutor` and preserve
  `InvocationContext`, `ExecutionTrace`, ownership, approval, and `runtime_id`.
- Preview traffic does not use localhost proxying, `runtime_port` identity, or a
  per-runtime `ALICE_PREVIEW_BASE_PATH`.

A proposal that conflicts with these invariants must be rebased or redesigned;
it must not establish a competing layer.

## Required merge order

1. **#348 — strict pytest configuration.** Review and merge independently if its
   green checks remain current. It removes a shared CI/configuration blocker.
2. **#347 — gateway ownership and lifecycle policy.** Rebase after #348, resolve
   its lifecycle review findings, and require a fully green Application tests
   job. This is the authorization/lifecycle prerequisite for revision loading.
3. **#349 — branch-aware RuntimeLoader.** Rebase after #347, resolve its loader
   capability, builtins, symlink, initialization-order, and rollback findings,
   then require the two-revision isolation suite and all CI checks to pass.
4. **#341 — host-managed preview smoke check.** Replace the old per-preview
   deployment assumptions only after #347 and #349 land. CI must ask the running
   host to load the exact commit, validate the rendered shell and streaming, and
   always clean up through `EnvironmentManager`.
5. **Remaining runtime isolation slices.** Land as separate PRs in this order:
   filesystem/source-data boundaries; tools/MCP; conversation/configuration;
   events/traces; revision HTTP/application streaming contract.

No dependent preview/runtime change may merge before #347 and #349. In
particular, #341 must not be “fixed” by restoring containers, subprocesses,
localhost transport, or process-global preview configuration.

## Live blockers

At the status time above:

- #347 is mergeable but its **Application tests** check is failing. Its branch
  still needs the reviewed inactive-environment status semantics and complete
  cleanup after post-start failures.
- #349 is mergeable but its **Application tests** coverage gate is failing. It
  also depends on #347 and must close the reviewed branch-loader security and
  lifecycle gaps before merge.
- #341 is based on a pre-threaded commit and its **Application tests** formatting
  check is failing. It requires a redesign and rebase after #347/#349, not a
  narrow CI-only repair.
- #336 and #331 predate the current runtime chain. Rebase them independently;
  neither should be used to carry runtime architecture changes.
- Dependency PRs #319–#322 stay separate and are rebased immediately before
  review. Do not mix their upgrades into architecture work.

Green or mergeable metadata does not override unresolved review findings,
dependency order, or required checks. Nothing in this plan authorizes automatic
merge.

## Workstream ownership and collision rules

| Workstream | Owns | Must consume, not duplicate |
| --- | --- | --- |
| #350 / runtime slices | Lifecycle, dispatcher capabilities, scoped resources, revision application contract | Existing `EnvironmentManager`, `RuntimeDispatcher`, and runtime policies |
| #227 / #223 | Progressive shell, frontend lifecycle, payload budget | Host gateway and event contracts; no alternate transport |
| #326 / #238 | MCP compatibility and external control-plane adapters | `UniversalToolExecutor`, runtime authorization, invocation/trace correlation |
| #254 | Plugin manifest, persistence, capability binding, lifecycle | Dispatcher capabilities and canonical tool execution |
| #256 | Provider-neutral coding-agent workflow and Codex adapter | Plugin platform, scoped Git/filesystem/process tools; never auto-merge |
| #340 | Provider-neutral storage and Google Drive adapter | Dispatcher authorization, canonical tools, trace correlation |
| #116 | Conversation-owned agent identity/state/permissions | Existing conversation model, runtimes, MCP/tools, invocation/traces |
| #195 / #104 | Signing operations and release process | Stable architecture and existing debug/release signing separation |

When two PRs touch the same boundary, keep the earlier prerequisite PR as the
owner. The later PR must rebase and adapt to its public contract rather than add
an alternate executor, registry, event bus, filesystem abstraction, runtime
identity, or lifecycle manager.

## Rebase and readiness checklist

Before requesting merge for any workstream:

1. Rebase onto the last merged prerequisite from the order above.
2. Compare overlapping files and remove parallel implementations of an owned
   layer.
3. Run focused behavioral tests for authorization, cross-runtime isolation,
   failure rollback, cleanup, cancellation/streaming, and trace redaction as
   applicable.
4. Run the repository architecture validators and full relevant CI without
   lowering coverage or policy gates.
5. Record migration, compatibility, and security impact in the PR description.
6. Update issue #343 with the result, next unblocked PR, and any required
   rebases. Do not merge automatically.

## Backlog assumptions to reject

The following approaches are obsolete under #350: a container or Flask process
per preview; localhost or `runtime_port` routing; per-preview process environment
mutation; threads described as a sandbox; direct runtime access to database,
filesystem, network, process, MCP, tools, events, or traces; and separate plugin,
agent, or MCP execution universes.
