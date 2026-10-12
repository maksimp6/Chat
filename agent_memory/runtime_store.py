"""Single process-owned registry for the native MemoryStore engine."""

from __future__ import annotations

from pathlib import Path
from threading import RLock

from memory_engine.store import MemoryStore

_REGISTRY_LOCK = RLock()
_STORES: dict[Path, MemoryStore] = {}


def get_runtime_memory_db(path: str | Path) -> MemoryStore:
    resolved = Path(path).expanduser().resolve()
    with _REGISTRY_LOCK:
        store = _STORES.get(resolved)
        if store is None:
            store = MemoryStore(resolved)
            _STORES[resolved] = store
        return store


def clear_runtime_memory_db(path: str | Path | None = None) -> None:
    """Close owned handles before forgetting them, releasing writer locks."""
    with _REGISTRY_LOCK:
        if path is None:
            for store in _STORES.values():
                store.close()
            _STORES.clear()
            return
        resolved = Path(path).expanduser().resolve()
        store = _STORES.get(resolved)
        if store is not None:
            store.close()
            del _STORES[resolved]


__all__ = ["clear_runtime_memory_db", "get_runtime_memory_db"]
