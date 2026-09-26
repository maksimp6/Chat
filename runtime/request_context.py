"""Request-scoped runtime metadata for the single-process preview host."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


_runtime_id: ContextVar[str | None] = ContextVar("alice_runtime_id", default=None)
_runtime_base_path: ContextVar[str] = ContextVar("alice_runtime_base_path", default="")


def current_runtime_id() -> str | None:
    """Return the runtime bound to the current execution context, if any."""
    return _runtime_id.get()


def current_runtime_base_path() -> str:
    """Return the URL prefix for the current runtime request."""
    return _runtime_base_path.get().rstrip("/")


@contextmanager
def bind_runtime_request(runtime_id: str, base_path: str) -> Iterator[None]:
    """Bind runtime metadata without mutating process-global environment state."""
    runtime_token = _runtime_id.set(str(runtime_id))
    base_token = _runtime_base_path.set(str(base_path or "").rstrip("/"))
    try:
        yield
    finally:
        _runtime_base_path.reset(base_token)
        _runtime_id.reset(runtime_token)
