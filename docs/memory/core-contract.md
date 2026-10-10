# Memory DB v1

The library owns a durable journal, not application data semantics.

Public API: `MemoryStore(path)`, `get(name)`, `set(name, value)`, `commit()`, `close()`.

`set` stages changes; `get` sees staged values. `commit` atomically confirms them and returns the sequence number. Empty commits do not advance it. Uncommitted values are discarded on close. Values must be JSON-compatible. One writer owns each path. Backup and restore are separate maintenance operations.

The public exports are `MemoryStore`, `Commit`, and `StoreError`. Internal journal classes are not public APIs.

This task excludes Alice integration, PyPI, Make and performance optimization.

Missing names return `None`; storing `None` is rejected to avoid ambiguity. After an uncertain fsync failure, the current instance refuses further writes. Reopening validates the complete hash-chained journal: a complete valid frame is replayed, while a partial or corrupt frame fails closed. A failed fsync is never acknowledged as a successful commit.
