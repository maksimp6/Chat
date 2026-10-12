# Issue #776 — Wave 1 durability and shadow migration proof

Status: **experimental / shadow-only**. SQL remains authoritative. No runtime
routing or storage cutover is authorized. Baseline master 51aaf8273f4f465fe78aa0a79e76a22d8f141e35;
migration inventory is in Draft PR #1172.

## FileMemoryDB durability contract

- One checksummed JSONL data file remains authoritative.
- A persistent, same-directory lock file provides advisory POSIX flock
  serialization for recovery, reads, writes, compaction and backup export.
  NEVER unlink the lock file while a process may still have it open.
- Every process must cooperate through FileMemoryDB, on the same local POSIX
  filesystem. Paths are canonicalized before deriving lock identity, so a
  symlink alias cannot introduce a competing lock. Network filesystems and
  object-store mounts are not proven.
- Every operation replays the file under the lock. This is intentionally
  correctness-first and O(file size) per operation; throughput is not accepted.
- ACK follows data fsync; first creation/compaction also fsync the parent.
  Compaction uses a temporary snapshot plus atomic rename.
- On append or compaction I/O failure the handle is poisoned: no further
  mutations are allowed until a new handle reopens/reconciles state. A complete
  but unacknowledged record may still survive after a failed fsync; callers
  must supply idempotent invocation IDs. Exactly-once execution is NOT proven.
- Torn final records are discarded, but complete corrupted records fail closed.
  Backup captures payload + sequence atomically under the same cross-process
  lock; restore verifies SHA-256 and sequence in an isolated destination.
  Restore also locks its target path to reject concurrent restorations and
  dangling symlink targets; backup fsyncs its own parent directory.
- SHA-256 is unkeyed corruption detection, not adversarial integrity/KMS.
- A process-shared file lock is not a security boundary against uncooperative
  actors, root, direct file writes or a foreign filesystem. FileMemoryDB
  supports single-key writes, not full multi-key transactions.

## Fault and recovery tests

Focused tests use temporary directories only. They cover a child exiting
immediately after an acknowledged write, four independent writers with a
concurrent compactor, monotonic sequences, cross-instance visibility, crashing
lock holder, ENOSPC/torn append, ambiguous fsync, corruption refusal,
compaction failure, defensive read copies, consistent backup/restore while
another writer is active, two simultaneous restore attempts, symlink refusal
and refusal to back up poisoned handles. No real SQL, RDC, Cloud.ru or phone was changed.

## Wave 1 shadow-only rehearsal

A new independent module accepts externally supplied, transactionally
consistent legacy rows. It does NOT open any live SQL connections.

- Session manager: session ID, lifecycle status, JSON metadata and timestamps.
- Memory manager/extractor: global memory fact rows, category, unique text,
  timestamps and memory configuration.
- Validate source revision, schema, duplicates and nested sensitive-key
  metadata before writing to a new isolated file.
- Store under clearly prefixed shadow namespaces; write a manifest LAST.
  Reopen and compare every record, counts, digest and source revision.
- The marker sets authority=shadow_only and owner_binding=unverified.
  A partial stage, source drift or broken digest is never accepted.
- Legacy session table lacks a trusted owner binding. That ownership must
  be resolved separately before any production migration; do not assume that
  creator, runtime or bearer token implies the session owner.
- Identity and conversation ownership already have typed #776 stores.
  Do not duplicate their authority or store credential values outside #755.

## Remaining gates before any runtime migration

1. Independent review of exact-head CI and failure-injection behavior.
2. Production-like filesystem crash/power-loss and multi-process evidence;
   test stale lock file / process restart on the target host.
3. An authorized read-only repeatable SQL source snapshot, owner mapping,
   secrets policy and shadow source catch-up; no partial mixed authority.
4. Shadow count, digest, ownership, restart and isolated backup/restore parity.
5. New separate PR for each typed session/memory runtime consumer and
   independent E2E acceptance. Only then can SQL cleanup Wave 4 begin.

This PR keeps legacy SQLite/PostgreSQL, existing CI and required checks intact.
**NO MERGE / NO CUTOVER / NO RDC or Redmi 9 changes.**
