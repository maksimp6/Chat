"""Runtime-facing storage capability; all I/O is delegated to RuntimeDispatcher."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from .dispatcher import RuntimeDispatcher

TraceSink = Callable[[str, Mapping[str, Any]], None]


class RuntimeStorage:
    def __init__(self, dispatcher: RuntimeDispatcher, runtime_id: str, *, trace: TraceSink | None = None) -> None:
        self._dispatcher = dispatcher
        self._runtime_id = runtime_id
        self._trace = trace

    def _call(self, action: str, provider: str, payload: Mapping[str, Any]) -> Any:
        event = {"action": action, "provider": provider, "runtime_id": self._runtime_id}
        if self._trace:
            self._trace("storage.started", event)
        try:
            result = self._dispatcher.dispatch_storage(self._runtime_id, provider, action, payload)
        except Exception:
            if self._trace:
                self._trace("storage.failed", event)
            raise
        if self._trace:
            self._trace("storage.completed", event)
        return result

    def upload(self, provider: str, object_id: str, content: bytes):
        return self._call("upload", provider, {"object_id": object_id, "content": content})

    def download(self, provider: str, object_id: str) -> bytes:
        return self._call("download", provider, {"object_id": object_id})

    def list(self, provider: str, prefix: str = ""):
        return self._call("list", provider, {"prefix": prefix})
