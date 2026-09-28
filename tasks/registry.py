"""Task handler registry shared by the web process and the worker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

_HANDLERS: dict[str, Callable[["TaskContext"], Any]] = {}


@dataclass
class TaskContext:
    """What a handler sees: the job payload and a way to publish progress."""

    job_id: str
    kind: str
    payload: dict[str, Any]
    queue: Any

    def emit(self, event: dict[str, Any]) -> None:
        self.queue.append_event(self.job_id, event)


def register_task(kind: str):
    """Register ``handler(ctx) -> result`` for jobs of ``kind``."""

    def decorator(handler):
        _HANDLERS[kind] = handler
        return handler

    return decorator


def get_task_handler(kind: str):
    handler = _HANDLERS.get(kind)
    if handler is None:
        raise LookupError(f"no task handler registered for {kind!r}")
    return handler
