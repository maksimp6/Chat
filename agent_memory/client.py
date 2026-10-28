"""Logos-owned lifecycle for the standalone explicit-commit Kristall.

The engine imports no Logos code. Callers share a single writer per canonical
path, use transactions for concurrent mutations, and close at runtime shutdown.
"""

from pathlib import Path
from threading import RLock
from typing import Any

from agent_memory.host import bound

_LOCK = RLock()
_CLIENTS: dict[Path, Any] = {}


def get_memory_client(path: str | Path) -> Any:
    resolved = Path(path).expanduser().resolve()
    with _LOCK:
        client = _CLIENTS.get(resolved)
        if client is None:
            client = bound.kristall_Kristall(resolved)
            _CLIENTS[resolved] = client
        return client


def close_memory_clients(path: str | Path | None = None) -> None:
    """Release application writers after consumers have stopped."""
    with _LOCK:
        paths = list(_CLIENTS) if path is None else [Path(path).expanduser().resolve()]
        for resolved in paths:
            client = _CLIENTS.get(resolved)
            if client is not None:
                client.close()
                del _CLIENTS[resolved]
