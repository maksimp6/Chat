# Memory DB architecture — target and migration (2026-10-11)

Status: **proposed target**, not a claim of completed SQL-free cutover. Tracks #776, #1045, #1046, #913 and related PRs.

## Boundaries and sources of truth

- GitHub issues, PRs, commits and reviews remain authoritative for development decisions.
- Execution Trace remains authoritative for observed model/tool/runtime events.
- Memory DB is authoritative only for its own committed application state. Derived agent memories retain source references, versions, expiry and visibility; stale facts must not override their sources.
- Secrets and raw credentials are never stored in agent memory.

## Target data path

```text
Alice API / agent adapters
         |
 typed namespaces + authorization + serialization
         |
 Memory DB (single writer per store, explicit commit)
         |------------------------|
 RAM working state          append-only journal
         |                        |
         +------ verified replay--+
                     |
          atomic snapshot / compaction
                     |
       encrypted, integrity-checked backup
                     |
              Cloud.ru object storage
```

The file store must use persistent mounted storage, **not** an ephemeral container layer. RAM is a cache/working set, not the sole durable copy. Cloud backup is asynchronous disaster recovery, not a substitute for locally durable commits.

## Transaction and recovery contract

- **Proposed API semantics (must be matched to implementation before acceptance):** `set` and `delete` stage changes in the writer's private transaction; `get` in that writer reads its own staged values, while other readers see only the last committed generation. `commit` publishes the complete new generation only after its durability barrier succeeds. Aborted or crashed uncommitted changes are never visible to other readers or replayed. Snapshot readers pin one committed generation to avoid mixed-version reads.
- A successful commit must have a durable journal record (including integrity framing/checksum) and required fsync of file and directory metadata where applicable before acknowledging success.
- Recovery replays only complete, verified committed transactions. Torn tails, corruption, duplicate/replayed operations, invalid versions and oversized records must have bounded, deterministic handling.
- If write/fsync returns an error or acknowledgement is lost, the outcome is **indeterminate**, not automatically rolled back: the transaction may already be durable. Fence that writer and reject subsequent writes/acks; reopen and verify the transaction ID against durable replay before returning committed/not-committed. Retry must use the same idempotency key and must not duplicate effects. Never silently treat an unverified write as successful.
- **Proposed snapshot publish protocol:** write a new generation to a temporary file; flush and fsync it; verify checksum/schema; atomically rename to an immutable generation filename; fsync the containing directory; write/fsync a temporary manifest referencing that generation and its journal boundary; atomically replace the active manifest; fsync its directory. Keep the previous manifest/generation and required journal segments until the new manifest is durable and independently recoverable. Crash at any step must recover either the previous complete generation or the new complete generation, never a mixture. Filesystem rename/fsync guarantees must be validated on the deployment volume.
- Enforce single-writer ownership, process restart semantics, size/record limits and namespace isolation. Multi-process writers require an explicit coordination design, not assumed safety.

## Backup and restore

- Encrypt backups **before** uploading to object storage (envelope encryption; keys referenced via secret management, not bundled with the backup).
- Backup manifests record format/schema version, snapshot/journal boundaries, checksums and key identifier (never key material).
- Restore verifies authenticity/integrity and complete journal coverage in an isolated location before promotion. Test wrong key, tampering, missing objects, interrupted upload and rollback.
- Define measurable RPO/RTO from tested backup cadence and restore times; do not claim zero loss for asynchronous backups.

## Migration from SQL

1. Inventory each SQL consumer (identity, ownership, sessions, profiles, conversation history, extracted facts and agent memory) and its authoritative schema.
2. Implement typed file-native adapters and deterministic import; preserve existing SQL behavior while migrating.
3. Shadow read/compare and reconcile row counts, keys, versions, access control and deletes. Missing values are not zero.
4. Cut over each aggregate only after verified equivalence, migration marker and recovery test; keep rollback available.
5. Remove SQL runtime dependencies only after all consumers and CI are verified. No premature global SQL-free claim.

## CI interpretation

- A job or workflow reported as `SKIPPED` was not executed; it is **not** a test failure. For docs-only changes, path-filtered application/integration jobs may legitimately skip.
- `SUCCESS` on the aggregate required CI job means only that the configured gate passed. It does not prove that skipped Memory DB fault-injection tests ran.
- Before implementation acceptance, verify branch protection's actual required checks, exact head SHA and evidence from relevant executed tests. Do not infer merge readiness from a documentation PR.

## Verification gates

- Unit/property tests for get/set/delete/commit, ordering, JSON value consistency, size limits and authorization.
- Fault injection at every write/fsync/rename/compaction boundary; crash/restart and replay tests.
- Concurrent readers, writer fencing, namespace isolation and exact-head CI.
- Backup encryption and isolated restore drill with explicit pass/fail evidence.
- Alice integration E2E plus legacy regression suite. A 2-second contract lane is a performance target, not a replacement for full correctness tests.

## Integration and rollout

Use feature branches -> PR into `develop` -> independent review and required CI -> separately approved PR into `master`. No direct master pushes, automatic cutover or merge from this document. Existing PR #1123 (commit/task adapter), #1083 (replay failure paths), and #1106 (CI contract) need independent exact-head verification before implementation status can be upgraded.

Related documents: [memory overview](../memory/overview.md), [agent memory](agent-memory.md), [platform architecture](../platform/architecture.md).
