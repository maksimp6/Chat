"""Single process-local registry for the accepted RAM-first MemoryStore."""

from __future__ import annotations

from pathlib import Path

from agent_memory.client import close_memory_clients, get_memory_client
from memory_engine import MemoryStore


def get_runtime_memory_db(path: str | Path) -> MemoryStore:
    """Return the shared durable MemoryStore for the canonical path."""
    return get_memory_client(path)


def clear_runtime_memory_db(path: str | Path | None = None) -> None:
    """Close and forget shared writers; never silently discard active writes."""
    close_memory_clients(path)


__all__ = ["clear_runtime_memory_db", "get_runtime_memory_db"]
