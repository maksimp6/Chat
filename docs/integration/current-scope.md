# Current-scope integration staging

This is the canonical staging ledger for the focused work coordinated by
[#351](https://github.com/maksimp6/Chat/issues/351). It records dependency and
admission decisions; it is not a replacement for focused pull requests and it
does not authorize automatic merging.

The architecture decisions in
[#350](https://github.com/maksimp6/Chat/issues/350) remain authoritative, while
[#343](https://github.com/maksimp6/Chat/issues/343) coordinates execution. The
detailed ownership and ordering rules live in
[Architecture integration coordination](../architecture/integration-coordination.md).

## Admission rules

A focused pull request may be staged only when it:

1. is updated from current `master` after its dependencies;
2. has completed required review and has green required checks;
3. preserves the host-managed `RuntimeLoader` and dispatcher-owned runtime
   resource model;
4. does not introduce a per-preview Flask process or container, localhost
   proxying, `runtime_port` transport, or process-global preview paths;
5. keeps tool execution behind `UniversalToolExecutor` and runtime resources
   behind `RuntimeDispatcher`; and
6. remains independently reviewable on its own branch.

Failures found in combined testing belong in the focused pull request that owns
the behavior whenever practical. Integration-only workarounds must not be used
to admit red work. Conflict resolutions must follow #350 rather than combine
competing abstractions mechanically.

## Foundation baseline

Runtime foundation [#363](https://github.com/maksimp6/Chat/pull/363) is merged.
The authoritative baseline is `master` commit
`64611a1900891aad0f421c94e73ffc7162942fc3`, which contains both the #347
runtime-owner policy and the #349 `RuntimeLoader` foundation. Those changes are
dependencies of the remaining runtime work, not pending staging candidates.

Repository cleanup on `master` is also authoritative. Integration must not
restore removed root files or `runtime_marker.txt`.

## Candidate ledger

Status recorded on 2026-09-27 against the foundation baseline above. Live CI,
review, and mergeability must still be checked immediately before admission.

| Order | Candidate | Staging decision / required action |
| --- | --- | --- |
| 1 | #364 staging ledger and #365 architecture coordination | Replace the two stale documentation heads with one synchronized documentation change containing this ledger and the architecture coordination document. |
| 2 | #359 host-managed preview lifecycle | Rebase on #363 and adapt to `RuntimeLoader`; reject container-per-preview, localhost proxying, `runtime_port`, and process-global preview paths. |
| 3 | #360 degraded frontend shell | Rebase independently; retain BrowserShim/VM coverage and repository-local assets, with no Playwright or CDN dependency. |
| 4 | #361 MCP dispatcher-scoped calls | Use the merged dispatcher and `UniversalToolExecutor`; do not introduce another dispatcher or tool execution boundary. |
| 5 | #366 storage | Consume dispatcher-scoped storage after #361 where tool/MCP behavior overlaps. |
| 6 | #369 conversation-agent foundation | Extend existing conversation identity and execution boundaries rather than creating a parallel agent system. |
| 7 | #370 runtime tool/MCP isolation | Reconcile with #361. Keep one executor and one dispatcher abstraction; the narrower accepted contract survives if the implementations compete. |
| 8 | #377 plugin execution contract | Map plugin capabilities onto the accepted executor and dispatcher contracts. |
| 9 | #378 filesystem isolation | Use dispatcher-owned runtime scope and coordinate with storage rather than adding direct filesystem access. |
| Independent | #374 Android release hardening | May synchronize independently while preserving debug/release signing separation and keeping secrets outside Git. |

If two candidates materially overlap, select the implementation that consumes
the merged foundation and has the narrowest authoritative ownership boundary.
Document why the other candidate is superseded instead of stacking both.

## Combined validation

After every admitted candidate, record its pull request and exact commit here.
Run its focused tests, then the repository checks:

```console
python -m compileall -q .
python tests/validate_runtime_modules.py
pytest -q
bash scripts/format.sh check
git diff --check
```

Run frontend validation for frontend changes and the prescribed Android unit
test and debug assembly for Android or shared release changes. Schema changes
must validate both SQLite and PostgreSQL. Security-sensitive changes require
authorization, isolation, cleanup, and secret-redaction regression coverage.

