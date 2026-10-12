"""Process-local registry for the authoritative file-native Memory DB.

Wave 1 consumers must share one FileMemoryDB object for a resolved path so they
share the same write lock and monotonic sequence. The deployment contract still
requires a single writer process; this registry is that process-local barrier.
"""

from __future__ import annotations

from pathlib import Path
from threading import RLock

from agent_memory.file_memory_db import FileMemoryDB

_REGISTRY_LOCK = RLock()
_STORES: dict[Path, FileMemoryDB] = {}


def get_runtime_memory_db(path: str | Path) -> FileMemoryDB:
    resolved = Path(path).expanduser().resolve()
    with _REGISTRY_LOCK:
        store = _STORES.get(resolved)
        if store is None:
            store = FileMemoryDB(resolved)
            _STORES[resolved] = store
        return store


def clear_runtime_memory_db(path: str | Path | None = None) -> None:
    """Forget cached instances; intended for recovery/tests, not normal writes."""
    with _REGISTRY_LOCK:
        if path is None:
            _STORES.clear()
            return
        _STORES.pop(Path(path).expanduser().resolve(), None)


__all__ = ["clear_runtime_memory_db", "get_runtime_memory_db"]
