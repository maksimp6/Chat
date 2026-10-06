# Memory DB v1 durability and migration contract

Status: implementation contract for issue #776.

## Purpose

Memory DB is the durable file-native storage engine for Alice. Alice depends on this API; the engine must not depend on Alice application code. Application and agents must never parse journal/checkpoint files directly.

The working source of truth lives under a runtime-specific data root. Remote object/cloud storage is backup, not the live filesystem.

The append-only file is the only authoritative live database file.

## Single-file layout

Memory DB v1 has one authoritative append-only file per runtime:

```text
/data/alice/alice.memory
```

Normal mutations append one checksummed JSONL record. There are no generation directories, journal segments, or one-file-per-event layouts.

Each durable record carries a format version, monotonically ordered sequence, operation, key/value payload when applicable, and integrity checksum. One process owns the write lock.

The only auxiliary file permitted is a short-lived `alice.memory.tmp` during compaction. It is fsynced and atomically renamed over the authoritative file, then the parent directory is fsynced.

## Synchronous write contract

Memory DB v1 has one authoritative writer and one database write barrier.

A mutating operation is:

1. acquire the database write lock;
2. validate and prepare the complete logical mutation without publishing it;
3. append its ordered journal representation;
4. flush userspace buffers;
5. fsync the journal data required for the commit;
6. durably publish the commit;
7. update/publish the committed in-memory view;
8. release the lock;
9. return success.

There is no timer, sleep, delayed flush, relaxed durability window or intentional group-commit wait in v1.

A successful write is a durability promise: after success is observable by the caller, a process crash must not remove that committed logical mutation.

If append, flush, fsync, allocation or commit fails, success must not be returned and the uncommitted state must not become visible.

Concurrent mutations are serialized. Reads must either wait for the write barrier or read the last completely committed immutable view. They must never observe a partial mutation.

External model, browser, network and tool work does not hold the DB write lock. Only the shortest storage critical section may hold it.

## Crash and corruption recovery

Startup does not report ready until recovery and integrity verification complete.

Recovery replays the authoritative file from the beginning. A truncated final record is uncommitted and may be discarded by truncating the file back to the last verified newline boundary. A checksum or format failure before the tail is corruption and must fail recovery rather than being ignored.

Re-running recovery must converge to the same logical state. A committed record may not silently disappear.

Checksum/integrity failures are never ignored. Suspect data is quarantined and the engine reports a degraded/error state rather than inventing a successful recovery.

ENOSPC/read-only/fsync failures are explicit write failures. They must not publish an in-memory success that cannot be recovered.

## Compaction

Compaction acquires the same write lock as ordinary mutations. It writes one checksummed snapshot record containing the current logical state and current sequence to `alice.memory.tmp`, flushes and fsyncs it, atomically replaces `alice.memory`, and fsyncs the parent directory.

Until the atomic replace succeeds, the old authoritative file remains valid. If compaction fails, the old file remains the source of truth.

## Backup and restore

A backup is complete only after:

`consistent snapshot -> manifest -> checksums -> upload -> remote verification -> commit marker`.

Initial machine-oriented remote target is Cloud.ru S3/Object Storage. Yandex Disk and Google Drive may be additional independent targets.

A backup that has not passed restore verification is not considered proven recoverable.

## Format migration

Migration never mutates the authoritative source generation in place.

Before migration, create a consistent source snapshot/inventory. A candidate generation must account for every logical source record.

Activation is forbidden unless verification reports:

```text
missing = 0
unexpected = 0
corrupted = 0
conflicts = 0
unverified = 0
```

Equivalence is logical/API equivalence, not byte equality between formats.

### Online catch-up

For migration v1 to v2:

1. v1 remains authoritative at sequence N;
2. create a consistent snapshot at N;
3. build v2 candidate from that snapshot;
4. Alice continues writing to v1;
5. v2 replays ordered events N+1 onward and catches up;
6. run v2 in shadow verification while v1 remains authoritative;
7. compare logical reads/results and integrity;
8. acquire the normal DB write barrier for final cutover;
9. replay through the final v1 sequence and verify zero lag/mismatch;
10. atomically switch CURRENT from v1 to v2;
11. release the barrier.

The heavy migration must not require Alice downtime. Only the final barrier is allowed to pause state mutations.

After DB cutover, the existing Alice runtime must first operate successfully against the new DB format/API. Application/runtime rollout happens afterward. The old generation remains immutable rollback evidence for the documented observation period.

No partial migration is acceptable. A single unverified source record fails the migration.

## Release order

`DB candidate -> catch-up -> shadow verify -> DB cutover -> old Alice on new DB -> new Alice -> observation -> retire old generation`.

Do not combine a storage-format switch and application-runtime switch into one unobservable event.

## Required executable evidence

Tests must cover at least:

- deterministic append/read and monotonic ordering;
- concurrent writers cannot interleave logical records;
- readers cannot observe a partial commit;
- crash before acknowledgement never creates a falsely acknowledged record;
- crash after acknowledgement never loses the acknowledged record;
- fsync/ENOSPC/read-only failure does not publish the mutation;
- recovery is idempotent;
- a truncated final record is discarded safely;
- corruption before the tail fails recovery;
- compaction replacement is atomic and cannot remove the only recoverable state;
- migration rejects one missing, unexpected, corrupt, conflicting or unverified record;
- migration catch-up includes writes created while conversion is running;
- cutover uses the same write barrier as ordinary commits;
- old Alice runtime can operate against the newly activated storage generation;
- backup restore reconstructs equivalent logical state.

Fault tests must include forced process termination at storage stage boundaries. Numeric RPO/RTO/checkpoint thresholds are documented only after measured evidence.

## Container invariant

The same Alice image digest contains runtime, Memory DB engine, tests/self-check, recovery, migration and backup/restore tooling. Persistent data is not an image layer.

CI tests the digest that is later deployed; production must not rebuild a different artifact after validation.
