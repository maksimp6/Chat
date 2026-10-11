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

- `get` reads a documented view of working/committed state; `set` and `delete` stage mutations. `commit` is the explicit durability boundary.
- A successful commit must have a durable journal record (including integrity framing/checksum) and required fsync of file and directory metadata where applicable before acknowledging success.
- Recovery replays only complete, verified committed transactions. Torn tails, corruption, duplicate/replayed operations, invalid versions and oversized records must have bounded, deterministic handling.
- A failed or ambiguous write fences the writer from publishing more commits until verified recovery/reopen. Never silently treat an unverified write as successful.
- Snapshot publication is atomic and versioned; compaction preserves the previous recoverable generation until the replacement is verified.
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

## Verification gates

- Unit/property tests for get/set/delete/commit, ordering, JSON value consistency, size limits and authorization.
- Fault injection at every write/fsync/rename/compaction boundary; crash/restart and replay tests.
- Concurrent readers, writer fencing, namespace isolation and exact-head CI.
- Backup encryption and isolated restore drill with explicit pass/fail evidence.
- Alice integration E2E plus legacy regression suite. A 2-second contract lane is a performance target, not a replacement for full correctness tests.

## Integration and rollout

Use feature branches -> PR into `develop` -> independent review and required CI -> separately approved PR into `master`. No direct master pushes, automatic cutover or merge from this document. Existing PR #1123 (commit/task adapter), #1083 (replay failure paths), and #1106 (CI contract) need independent exact-head verification before implementation status can be upgraded.

Related documents: [memory overview](../memory/overview.md), [agent memory](agent-memory.md), [platform architecture](../platform/architecture.md).
