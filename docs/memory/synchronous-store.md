# Synchronous Memory Store — implementation slice

**Status:** development only (PR #1041). This is not the default Alice Pro database,
does not retire SQL, and is not production-accepted.

## Responsibility boundary

- `memory_engine.Store` describes the Python get/set/delete contract.
- `MemoryStore` owns committed in-process RAM state and one exclusive POSIX file lock.
- `Transaction` stages only changed keys. It does not copy the whole database or expose
  internal mutable values to callers.
- The journal owns durability; a successful commit is returned **after** the journal
  append and `fsync`. A newly created journal also fsyncs its containing directory.
- Read operations return copies of committed values. Uncommitted values are visible
  through the owning transaction handle only.

## Example

```python
from memory_engine import MemoryStore

with MemoryStore("/path/to/alice.memory") as store:
    store.set("settings", "theme", "gray")
    with store.transaction() as tx:
        tx.set("chats", "c1", {"title": "New chat"})
        tx.set("settings", "language", "ru")
    assert store.get("settings", "language") == "ru"
```

One transaction produces at most one append-only delta record. Each record carries
a monotonic sequence and chained SHA-256 digest. This detects accidental journal
corruption but is **not** a blockchain or a substitute for independent backups.

## Failure behavior

- Unsupported JSON values fail validation before journal mutation.
- A failed or ambiguous append/fsync does **not** acknowledge success; subsequent writes
  require recovery. The last confirmed RAM view remains visible during that process.
- A torn tail or corrupted committed frame makes startup fail closed rather than
  silently discarding possibly confirmed data.
- The writer file lock is local-host coordination, not a distributed consensus system.

## Not yet implemented / release blockers

The engine currently lacks atomic backup/restore, bounded checkpoints, automatic
validated recovery, precise typed domain records, Trace integration and replica
acknowledgement. SQL consumers still use their legacy paths. Future two-service
RAM mirroring must fit behind the same typed contract and must never permit
independent writes from an isolated replica.

Do not declare the implementation accepted until crash/fault-injection tests,
format/type checks, exact-head CI and functional verification are complete.
