"""Runtime-facing storage capability; all resource access goes through the dispatcher."""

from __future__ import annotations

from typing import Any

from .dispatcher import RuntimeDispatcher


class RuntimeStorage:
    def __init__(self, dispatcher: RuntimeDispatcher, runtime_id: str, trace: Any = None) -> None:
        self._dispatcher = dispatcher
        self._runtime_id = runtime_id
        self._trace = trace

    def upload(self, provider: str, name: str, content: bytes, *, resource_runtime_id: str | None = None) -> dict:
        return self._execute(provider, "upload", {"name": name, "content": content}, resource_runtime_id)

    def download(self, provider: str, object_id: str, *, resource_runtime_id: str | None = None) -> bytes:
        return self._execute(provider, "download", {"object_id": object_id}, resource_runtime_id)

    def list(self, provider: str, prefix: str = "", *, resource_runtime_id: str | None = None) -> list[dict]:
        return self._execute(provider, "list", {"prefix": prefix}, resource_runtime_id)

    def _execute(self, provider: str, action: str, values: dict, resource_runtime_id: str | None) -> Any:
        event = {"runtime_id": self._runtime_id, "provider": provider, "action": action}
        if self._trace is not None and hasattr(self._trace, "add_event"):
            self._trace.add_event("storage_operation_started", event)
        try:
            result = self._dispatcher.dispatch(
                self._runtime_id,
                "storage.execute",
                {"provider": provider, "action": action, **values},
                resource_runtime_id=resource_runtime_id,
            )
        except Exception as exc:
            if self._trace is not None and hasattr(self._trace, "add_event"):
                self._trace.add_event("storage_operation_failed", {**event, "error_type": type(exc).__name__})
            raise
        if self._trace is not None and hasattr(self._trace, "add_event"):
            self._trace.add_event("storage_operation_completed", event)
        return result
