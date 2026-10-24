---
name: durable-storage-evidence
description: Review durable-write, crash-recovery, backup and migration claims against the accepted Memory DB contract.
---

## Purpose
Keep engine proof, backup proof and consumer migration independently verifiable (H12).

## Non-goals
No live restore/cutover, new SQL compatibility layer or claim that process-local tables are durable storage.

## Inputs
Accepted storage contract, exact engine head, authoritative consumer inventory, disposable fixtures, failure model and expected invariants/digests.

## Tools
Existing typed storage APIs, fault-injection tests, fresh-process reopen and isolated backup/restore tests.

## Procedure
1. Identify the authority for each consumer and distinguish FileMemoryDB, process-local MemoryDatabase and proposed engines.
2. Prove one typed table: acknowledgement follows the required durable barrier; fresh-process reopen returns the committed state.
3. Test torn writes, corruption, ENOSPC and concurrency; failed mutations must not appear committed.
4. Check bounded replay and checkpoint/compaction preservation of the last valid state.
5. Restore a backup into an isolated instance and compare counts, typed invariants and deterministic digests.
6. Only after engine proof, plan dependency waves: import/catch-up with old authority, shadow verification, atomic cutover and explicit recovery.
7. If encryption is in scope, test unavailable/wrong key context and retained-backup decryptability without plaintext fallback; successful encryption alone is not restore acceptance.

## Approval boundaries
Only disposable local fixtures are implicit here. Production restore, destructive migration, key administration and authority cutover require their separate authorization.

## Validation
All required fault cases and fresh-process restore pass on the exact engine head; each migrated consumer has explicit authority evidence. No mixed-authority transition is called complete.

## Failure behavior
Keep Draft engines and legacy compatibility distinct. Missing durable barrier, skipped restore or unverified platform persistence blocks the corresponding claim.

## Output
Engine/backup/migration evidence matrix, current authority map, failure cases and next dependency wave.
Sources: [H12](../../../docs/lessons/README.md), [v1 contract](../../../docs/memory/memory-db-v1-contract.md), [#776](https://github.com/maksimp6/Chat/issues/776).
