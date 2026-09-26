"""Request-scoped runtime metadata for the single-process preview host."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


_runtime_id: ContextVar[str | None] = ContextVar("alice_runtime_id", default=None)
_runtime_base_path: ContextVar[str] = ContextVar("alice_runtime_base_path", default="")
_runtime_data_root: ContextVar[str | None] = ContextVar("alice_runtime_data_root", default=None)


def current_runtime_id() -> str | None:
    """Return the runtime bound to the current execution context, if any."""
    return _runtime_id.get()


def current_runtime_base_path() -> str:
    """Return the URL prefix for the current runtime request."""
    return _runtime_base_path.get().rstrip("/")


def current_runtime_data_root() -> str | None:
    """Return the private data root for the current runtime, if one is bound."""
    return _runtime_data_root.get()


@contextmanager
def bind_runtime_request(
    runtime_id: str,
    base_path: str,
    *,
    data_root: str | None = None,
) -> Iterator[None]:
    """Bind runtime metadata without mutating process-global environment state."""
    runtime_token = _runtime_id.set(str(runtime_id))
    base_token = _runtime_base_path.set(str(base_path or "").rstrip("/"))
    data_token = _runtime_data_root.set(str(data_root) if data_root else None)
    try:
        yield
    finally:
        _runtime_data_root.reset(data_token)
        _runtime_base_path.reset(base_token)
        _runtime_id.reset(runtime_token)
