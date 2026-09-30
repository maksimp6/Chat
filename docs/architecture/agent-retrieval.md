# Hybrid agent retrieval

Issue #579 adds a deterministic retrieval layer over the context cache, shared agent
memory, and the existing Python AST repository index.

The goal is to reduce repeated repository reads before a specialist model is called.

## Retrieval order

The service follows the project context policy:

1. exact context-cache hit;
2. reusable slices from a partial cache hit;
3. fresh task memory for the exact work item/head;
4. active role/project/process memory visible to the receiving role;
5. exact-head repository AST/symbol index;
6. raw source remains a later Coordinator fallback when the evidence bundle is
   insufficient.

An exact cache hit short-circuits lower layers. A partial hit keeps reusable slices and
continues retrieval for missing context.

## Ranking

Memory and code candidates use a local BM25-style lexical score. Small deterministic
boosts preserve source priority for exact task and role knowledge.

Embeddings are not required for correctness. They may later be added only as an
optional ranking signal behind the same freshness/provenance contract.

## Freshness

Task memory is fail-closed:

- GitHub repository must match;
- provenance work item must match;
- provenance head SHA must match the current query head.

Code-index evidence is used only when the index source version exactly matches the
query head SHA. A stale index therefore returns no code hits rather than pretending to
be current.

Project/process memory remains governed by the shared memory store's
active/stale/superseded/expiry state and GitHub provenance.

## Access control and secrets

Memory visibility is enforced by the shared memory store before ranking. Retrieval does
not bypass restricted point/list access.

Cached slices and memory records are already sanitized before this layer. Retrieval
telemetry stores only source counts, references and size/savings metadata, never the raw
query text.

## Bounds

A query specifies bounded result and character budgets. Results are sorted
deterministically and clipped before they become a model context bundle.

The service returns compact code summaries from the AST index, not source-file bodies.
Relevant test paths are preserved when the index knows them.

## Telemetry

`record_retrieval()` adds a bounded `hybrid_retrieval` event and
`retrieval_operations` record to Execution Trace:

- hit/miss;
- cache hit/partial/miss;
- source counts;
- source refs;
- result character count;
- saved source bytes and known input tokens from cache reuse.

This telemetry is intended for the Work Coordinator and Operations Observer to measure
whether retrieval actually avoids repeated context work.
