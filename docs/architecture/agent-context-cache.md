# Agent task context cache

Issue #577 introduces the first deterministic context layer for Alice Pro agents.

The cache exists to avoid repeated repository, CI, review and trace reads when the
authoritative evidence has not changed. It is an acceleration layer only.

## Source of truth

GitHub remains authoritative for development state:

- repository and branch state;
- issue and pull-request identity;
- base/head commits;
- reviews and review threads;
- CI/check results;
- merged policy, skills and code.

Execution Trace remains authoritative for recorded runtime evidence.

The context cache may summarize and reuse those facts, but it must not override them.

## Task packet

`TaskPacket` carries the compact handoff prepared before an agent/model call:

- repository/work item/base/head/role;
- selected skills;
- objective and expected deliverable;
- known facts, open questions and failed attempts;
- changed files;
- evidence references;
- bounded reusable context slices;
- owner, budget tier and usage-so-far;
- optional escalation target.

Secrets and inline credential patterns are sanitized before packet payloads are retained.

## Exact-head cache identity

The exact cache identity is derived from:

```text
repository
+ issue/PR work item
+ base SHA
+ head SHA
+ receiving role
+ selected skills
+ evidence versions
```

Changing base/head or role creates a different scope and therefore a full miss.

## Evidence versions and partial reuse

Evidence has independent versions for:

- CI;
- reviews;
- trace/reproduction;
- relevant file hashes;
- selected skill contents/versions;
- policy.

Each `ContextSlice` declares which evidence components it depends on.

If only one component changes, only slices depending on that component become stale.
Independent slices can be reused and the lookup reports `partial`.

Example:

```text
code slice    -> files + policy
CI slice      -> CI
review slice  -> review
skills slice  -> skills

new CI result:
code    reusable
CI      stale
review  reusable
skills  reusable
```

A new PR head never reuses an old exact-head packet.

## Telemetry

Each lookup can be recorded on the existing `ExecutionTrace` surface as a
`context_cache_lookup` event and `context_cache_operations` entry.

The record includes:

- `hit`, `partial` or `miss`;
- stale evidence components;
- reused slice names;
- source bytes avoided;
- known input tokens avoided;
- GitHub work-item/head provenance.

Token savings are recorded only when the producer supplied token counts. The cache does
not invent token estimates.

## Persistence boundary

The #577 cache is intentionally process-local.

Persistent shared memory belongs to #578. This avoids making the first cache a second
hidden source of project truth and keeps its semantics easy to test.

A future persistent layer may store packets, but it must preserve the same GitHub
provenance and freshness rules.

## Runtime integration

This slice defines the backend contract and deterministic cache behavior. The shared
Work Coordinator in #580 will own the orchestration rule:

1. exact cache;
2. task/shared memory;
3. retrieval/indexes;
4. raw source only for unresolved gaps.

The coordinator must not repeat expensive reasoning when fresh cached evidence already
answers the context need.
