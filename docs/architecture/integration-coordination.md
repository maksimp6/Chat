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

## Integrated order after #363

The integration sequence below is complete on `master`; the exact merge commits
are recorded in the [current-scope ledger](../integration/current-scope.md).

1. **Host-managed preview lifecycle (#359)** was integrated on top of the
   loader/owner foundation without restoring per-preview containers or
   `runtime_port` transport.
2. **Degraded frontend shell (#360)** retained BrowserShim/VM coverage and
   repository-local assets.
3. **Dispatcher-scoped runtime resources (#361, #366, #369, #370, #377 and
   #378)** converged on one `RuntimeDispatcher` and one
   `UniversalToolExecutor`.
4. **Android release hardening (#374)** merged independently while preserving
   the signing and secret boundaries.
5. **Runtime follow-ups (#382, #383 and #390)** completed stopped-state,
   revision-isolation and local-command behavior through the accepted runtime
   boundaries.

New follow-up work must start from current `master`; #363 is a historical
foundation SHA, not a branch target. No new candidate is admitted merely
because it merges cleanly. Its focused checks, validators, formatting, hosted
CI and mergeability must be green.

## Integrated ownership decisions

- **#361 and #370:** MCP calls use the shared dispatcher route; #370 adds
  isolation guarantees without a second registry, dispatcher or executor.
- **#366 and #378:** #366 owns the storage contract and #378 owns runtime
  filesystem isolation. Filesystem-backed storage uses dispatcher scope without
  a second direct-access path.
- **#369 and #377:** conversation agents and plugins invoke capabilities through
  the existing executor; neither owns a separate agent/tool execution universe.

Future implementations that overlap these boundaries must extend the integrated
owner instead of reviving a competing abstraction.

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
