# Hybrid retrieval for agent context

Issue #579 adds a deterministic retrieval layer above the exact-head cache and shared
agent memory.

The goal is to return the smallest source-attributed evidence packet instead of making
an agent reread the repository, CI logs, issue history or trace history.

## Retrieval order

The surrounding Coordinator (#580) owns the complete order:

1. exact TaskPacket/cache;
2. task memory;
3. role/project memory;
4. hybrid retrieval over normalized project indexes and summaries;
5. raw source only for unresolved gaps.

The retrieval service accepts preloaded evidence, so cache/memory results can be reused
without a second source read.

## Deterministic ranking

The first implementation uses a bounded BM25-style lexical scorer with a small metadata
boost for path/module/symbol matches.

It does not call a model.

Embeddings may later contribute an optional ranking signal, but correctness and
availability must not depend on a vector service.

## Sources

### Shared memory

`MemorySource` reads active #578 memory records and enforces:

- memory kind/scope filters;
- role visibility;
- sensitivity boundary;
- GitHub repository match;
- exact head for records explicitly marked `head_bound`.

Stale head-bound readiness/task facts are never returned as current evidence.

### Existing AI index

`AiIndexSource` consumes the existing deterministic `scripts/build_ai_index.py`
payload. It indexes metadata already extracted from Python files:

- path/module;
- symbols;
- imports/calls;
- linked tests;
- source hash.

Retrieval does not reread Python source.

The entire index is head-bound. A mismatched Git head returns no code-index evidence.

### Normalized summaries

`StaticSource` accepts already-sanitized GitHub, CI, trace or documentation summaries.
This is the adapter boundary for later source collectors. It applies the same
repository/head/visibility/sensitivity filtering.

## Budgets

Each query has:

- result-count limit;
- total character budget;
- optional source-type filter;
- role/memory scope;
- sensitive-content opt-in.

The first oversized result may be clipped to the character budget; later results never
overflow it.

## Provenance

Every returned document carries:

- stable document id;
- source type;
- source refs;
- source version;
- structured metadata;
- sensitivity/visibility;
- whether the document is exact-head bound.

No retrieved claim is intentionally detached from its source.

## Telemetry

`record_retrieval()` records a bounded `agent_retrieval` event and
`retrieval_operations` entry on the existing Execution Trace.

Telemetry includes:

- hit/miss;
- source counts;
- documents considered;
- result count/characters;
- stale/visibility/sensitivity filters;
- known source reads avoided;
- truncation.

The service never invents token savings. A caller may record source reads avoided only
when that saving is actually known.

## Boundaries

This slice does not:

- call an embedding API or LLM;
- crawl GitHub or raw repository files;
- replace the exact-head cache;
- mutate shared memory;
- make merge/readiness decisions;
- bypass source freshness.

The Work Coordinator in #580 will compose cache, memory and retrieval into the final
specialist task packet.
