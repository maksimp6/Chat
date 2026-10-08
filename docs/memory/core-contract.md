# Memory DB v1

The library owns a durable journal, not application data semantics.

Public API: `MemoryStore(path)`, `get(name)`, `set(name, value)`, `commit()`, `close()`.

`set` stages changes; `get` sees staged values. `commit` atomically confirms them and returns the sequence number. Empty commits do not advance it. Uncommitted values are discarded on close. Values must be JSON-compatible. One writer owns each path. Backup and restore are separate maintenance operations.

The public exports are `MemoryStore`, `Commit`, and `StoreError`. Internal journal classes are not public APIs.

This task excludes Alice integration, PyPI, Make and performance optimization.
