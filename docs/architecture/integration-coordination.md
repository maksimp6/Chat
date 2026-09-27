# Architecture integration coordination

This document is the canonical architecture coordination guide for the active
Alice Pro workstreams. Issue
[#350](https://github.com/maksimp6/Chat/issues/350) is authoritative for runtime
and deployment architecture; issue
[#343](https://github.com/maksimp6/Chat/issues/343) remains the umbrella
execution plan. Admission status is maintained only in the
[current-scope staging ledger](../integration/current-scope.md).

## Merged foundation

Pull request #363 merged the #347 runtime-owner policy and #349 `RuntimeLoader`
foundation at `64611a1900891aad0f421c94e73ffc7162942fc3`. Follow-up branches must
start from that baseline or newer `master`; they must not carry alternate copies
of either foundation.

The merged contract provides a branch-safe loader without `sys.path` or
`sys.modules` mutation and dispatcher-owned runtime authorization and resource
access. In-process Python threads are execution units, not security sandboxes.

## Non-negotiable boundaries

- `EnvironmentManager` owns environment lifecycle; `RuntimeLoader` loads the
  constrained revision entry point; `RuntimeDispatcher` owns scoped resource
  access and cross-runtime rejection.
- Revision loading must not mutate process-global import state, environment
  variables, or preview paths.
- Preview traffic must not use a per-preview Flask process or container,
  localhost proxying, or `runtime_port` transport.
- Tool and MCP calls converge on `UniversalToolExecutor` and preserve
  `InvocationContext`, `ExecutionTrace`, owner, and runtime correlation.
- Plugins, storage providers, conversation agents, and coding-agent backends
  consume these boundaries; they must not create parallel execution,
  authorization, tracing, or resource layers.
- Repository cleanup is authoritative. Removed root files and
  `runtime_marker.txt` must not be restored by a rebase or conflict resolution.

## Required order after #363

1. **Documentation coordination (#364/#365).** Keep exactly one staging ledger
   and this architecture coordination guide, synchronized to the merged
   foundation.
2. **Host-managed preview lifecycle (#359).** Adapt lifecycle and gateway
   behavior to the merged loader and owner policy. Do not restore obsolete
   preview deployment or transport mechanisms.
3. **Degraded frontend shell (#360).** Preserve progressive enhancement using
   BrowserShim/VM tests and repository-local assets. This may be reviewed after
   synchronization without redefining runtime transport.
4. **Dispatcher-scoped runtime resources.** Stage #361, #366, #369, #370,
   #377, and #378 only after resolving ownership overlap. There must be one
   `RuntimeDispatcher` and one `UniversalToolExecutor`, not competing tool or
   dispatcher abstractions.
5. **Android release hardening (#374).** This is independent of the runtime
   sequence, subject to signing-boundary and secret-handling requirements.

No candidate is admitted merely because it merges cleanly. Its focused checks,
full suite, validators, formatting, hosted CI, and mergeability must be green.
No step in this sequence should be auto-merged.

## Overlap ownership

- **#361 and #370:** #361 owns routing MCP calls through dispatcher scope;
  #370 should add isolation guarantees on that same route. If #370 contains a
  second registry, dispatcher, or executor, retain #361's shared-boundary
  approach and port only non-duplicative isolation tests or behavior.
- **#366 and #378:** #366 owns the storage contract; #378 owns runtime
  filesystem isolation. Filesystem-backed storage must use dispatcher scope,
  without a second direct-access path.
- **#369 and #377:** conversation agents and plugins both invoke capabilities
  through the existing executor. Neither owns a new agent/tool execution
  universe.

When implementations still compete after rebase, prefer the smaller change
that directly consumes #363 and preserves correlation and runtime scope.
Supersede the alternative explicitly rather than layering both.

## Readiness gates

Before a pull request is marked ready:

1. Update it from current `master` after its direct dependency.
2. Confirm it contains no per-preview process/container, localhost proxy,
   `runtime_port`, process-global preview path, or direct cross-runtime access.
3. Add behavioral coverage for authorization, rollback, cancellation, cleanup,
   and two-runtime isolation where applicable.
4. Run focused tests, the full backend suite, runtime/static validators, the
   formatter check, and required GitHub CI without lowering coverage gates.
5. Validate both SQLite and PostgreSQL for schema changes and run the prescribed
   Android checks for Android changes.
6. Record the exact commit, migration effects, and any overlap decision in the
   staging ledger. Do not merge to `master` without required review.

## Superseded assumptions

The following designs must not return through a follow-up or conflict
resolution:

- one container or Flask subprocess per preview;
- localhost or `runtime_port` routing between the gateway and a runtime;
- per-runtime mutation of `ALICE_PREVIEW_BASE_PATH` or other process globals;
- arbitrary branch imports through global `sys.path`/`sys.modules` changes;
- threads presented as protection from hostile Python code;
- direct runtime access to database, filesystem, network, process, MCP, tools,
  conversations, events, or traces; and
- separate MCP, plugin, conversation-agent, or autonomous-agent execution
  universes.

Useful requirements from older proposals belong behind the accepted host,
loader, dispatcher, executor, and trace contracts rather than behind obsolete
deployment mechanisms.

