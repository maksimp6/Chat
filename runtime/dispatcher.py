"""Scoped execution boundary for Alice Pro preview runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock, local
from typing import Any, Callable, Mapping


class RuntimeScopeViolation(PermissionError):
    """Raised when one runtime attempts to access another runtime's resources."""


class RuntimeNotFound(KeyError):
    """Raised when a runtime id is not registered."""


class RuntimeOperationNotFound(KeyError):
    """Raised when a runtime operation is not registered."""


class RuntimeOwnerViolation(PermissionError):
    """Raised when a caller is not allowed to access a runtime."""


@dataclass(frozen=True)
class RuntimeContext:
    runtime_id: str
    owner_id: str | None
    namespace: str
    root: str | None = None


RuntimeHandler = Callable[[RuntimeContext, Mapping[str, Any]], Any]


class RuntimeDispatcher:
    """Authorize and execute resource operations for isolated preview runtimes.

    Runtime modules should not open shared resources directly. They ask this
    dispatcher to execute a registered operation in the caller's runtime scope.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._runtimes: dict[str, RuntimeContext] = {}
        self._operations: dict[str, RuntimeHandler] = {}
        self._local = local()

    def register_runtime(
        self,
        runtime_id: str,
        *,
        owner_id: str | None = None,
        namespace: str | None = None,
        root: str | None = None,
    ) -> RuntimeContext:
        runtime_id = str(runtime_id or "").strip()
        if not runtime_id:
            raise ValueError("runtime_id is required")
        context = RuntimeContext(
            runtime_id=runtime_id,
            owner_id=owner_id,
            namespace=namespace or f"runtime:{runtime_id}",
            root=root,
        )
        with self._lock:
            self._runtimes[runtime_id] = context
        return context

    def unregister_runtime(self, runtime_id: str) -> None:
        with self._lock:
            self._runtimes.pop(runtime_id, None)

    def register_operation(self, name: str, handler: RuntimeHandler) -> None:
        name = str(name or "").strip()
        if not name:
            raise ValueError("operation name is required")
        if not callable(handler):
            raise TypeError("operation handler must be callable")
        with self._lock:
            self._operations[name] = handler

    def context(self, runtime_id: str) -> RuntimeContext:
        with self._lock:
            context = self._runtimes.get(runtime_id)
        if context is None:
            raise RuntimeNotFound(runtime_id)
        return context

    def current_context(self) -> RuntimeContext:
        runtime_id = getattr(self._local, "runtime_id", None)
        if runtime_id is None:
            raise RuntimeNotFound("no runtime is bound to this thread")
        return self.context(runtime_id)

    def authorize(self, runtime_id: str, owner_id: str | None) -> RuntimeContext:
        """Resolve a runtime without exposing cross-owner access."""
        context = self.context(runtime_id)
        if owner_id and context.owner_id not in (None, owner_id):
            raise RuntimeOwnerViolation(runtime_id)
        return context

    def dispatch(
        self,
        runtime_id: str,
        operation: str,
        payload: Mapping[str, Any] | None = None,
        *,
        resource_runtime_id: str | None = None,
    ) -> Any:
        context = self.context(runtime_id)
        target_runtime_id = resource_runtime_id or runtime_id
        if target_runtime_id != runtime_id:
            raise RuntimeScopeViolation(
                f"runtime {runtime_id!r} cannot access resources owned by {target_runtime_id!r}"
            )

        with self._lock:
            handler = self._operations.get(operation)
        if handler is None:
            raise RuntimeOperationNotFound(operation)

        previous = getattr(self._local, "runtime_id", None)
        self._local.runtime_id = runtime_id
        try:
            return handler(context, dict(payload or {}))
        finally:
            if previous is None:
                try:
                    del self._local.runtime_id
                except AttributeError:
                    pass
            else:
                self._local.runtime_id = previous
