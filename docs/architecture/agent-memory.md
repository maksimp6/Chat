# GitHub-first shared agent memory

Issue #578 adds a persistent memory store for repository agents without creating a
second source of project truth.

## Source-of-truth rule

GitHub remains the development ledger for issues, pull requests, commits, reviews,
checks, merges, policies, skills and repository documentation.

Execution Trace remains authoritative for recorded runtime/tool/model activity.

Agent memory stores compact derived facts plus provenance. If a memory record conflicts
with its current source, the source wins and the memory is stale or superseded.

## Memory layers

The shared store supports five kinds:

- `task` — short-lived state for one issue/PR/objective;
- `project` — accepted architecture/integration facts;
- `role` — reusable procedures and failure patterns for a role;
- `process` — Observer/Governor escalations and process lessons;
- `user_status` — compact state needed to explain progress to the user.

Legacy `global_memory` remains separate in this slice. It predates provenance and
freshness contracts and is not silently imported into agent memory.

## Record contract

Each `MemoryRecord` includes:

- stable `memory_id`;
- kind and scope;
- compact sanitized text/payload;
- provenance with `source_type` and source refs;
- `source_version` such as a Git commit, trace version or external source revision;
- confidence;
- freshness/invalidation metadata;
- sensitivity classification and role visibility;
- active/stale/superseded status;
- created/updated timestamps;
- optional expiry and superseding record.

GitHub provenance additionally requires the repository name.

Nested payload/provenance values are immutable inside a record and are exported as
independent copies.

## Write policy

Durable project/role/process memory should represent merged or accepted decisions,
repeatedly confirmed facts, reusable procedures/skills, or stable process lessons.

Unconfirmed hypotheses belong in task memory and should expire instead of being
promoted into durable organizational knowledge.

## Freshness

A lookup returns:

- `hit` when the record is active, unexpired and matches the requested source version;
- `stale` when it was explicitly invalidated, expired or the source version changed;
- `superseded` when a newer memory record replaced it;
- `miss` when no record exists.

The store does not automatically overwrite current GitHub facts with remembered data.

## Storage

`AgentMemoryStore` uses the existing database abstraction:

- SQLite by default;
- PostgreSQL when `ALICE_DATABASE_URL` selects it.

The schema stores structured metadata as deterministic JSON text, so correctness does
not depend on a vector database or external retrieval service.

Hybrid retrieval/embeddings belong to #579.

## Redaction

Memory uses the shared trace sanitization boundary before storing text or structured
payloads. Common credential keys and inline authorization/token forms are redacted.

Raw credentials, private keys and access tokens must never be intentionally promoted
to memory even when the record sensitivity is `sensitive`.

## Export and rebuild

`export_records()` produces stable JSON-serializable records with provenance.

A replacement store can therefore be rebuilt from authoritative GitHub/trace sources
and then repopulated with accepted derived records. The database itself is not the only
place where the reason for a development decision may exist.

Organizationally meaningful new conclusions must still be written back to GitHub as an
issue, PR/comment, documentation change or skill.
