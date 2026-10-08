# Alice Memory DB

Standalone synchronous RAM-backed key/value storage with an append-only durable journal.

**Status: pre-release.** The public API is under review and must not be treated as stable.

## Example

```python
from memory_engine import MemoryStore

with MemoryStore("example.memory") as db:
    db.set("settings/theme", "gray")
    db.commit()
    assert db.get("settings/theme") == "gray"
```

The client owns the database instance and storage path. The library does not depend on Alice Pro or SQL. Journal integrity, crash behavior, exclusive writer locking and backups are covered by independent tests.

## Packaging

`make build` creates a wheel; `make test` runs the standalone tests. Release candidates must pass clean-install tests, archive inspection, static checks and functional review before any PyPI upload.

Source: https://github.com/maksimp6/Chat
License: MIT (repository root LICENSE).
