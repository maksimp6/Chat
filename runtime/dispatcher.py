"""Scoped execution boundary for Alice Pro preview runtimes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock, local
from typing import Any, Callable, Mapping

from universal_tool_platform import UniversalToolCall, UniversalToolExecutor
from storage import (
    StorageCredentialsMissing,
    StorageError,
    StorageProvider,
    StorageProviderUnavailable,
)


TOOL_EXECUTE_OPERATION = "tools.execute"


class RuntimeScopeViolation(PermissionError):
    """Raised when one runtime attempts to access another runtime's resources."""


class RuntimeNotFound(KeyError):
    """Raised when a runtime id is not registered."""


class RuntimeOperationNotFound(KeyError):
    """Raised when a runtime operation is not registered."""


class RuntimeOwnerViolation(PermissionError):
    """Raised when a caller is not allowed to access a runtime."""


FILESYSTEM_READ_TEXT = "filesystem.read_text"
FILESYSTEM_WRITE_TEXT = "filesystem.write_text"


@dataclass(frozen=True)
class RuntimeContext:
    runtime_id: str
    owner_id: str | None
    namespace: str
    root: str | None = None


RuntimeHandler = Callable[[RuntimeContext, Mapping[str, Any]], Any]
RuntimeToolPolicy = Callable[[RuntimeContext, Any, UniversalToolCall], Any]


@dataclass(frozen=True)
class _RuntimeToolBoundary:
    executor: UniversalToolExecutor
    policy: RuntimeToolPolicy | None = None
    approval: RuntimeToolPolicy | None = None


class RuntimeDispatcher:
    """Authorize and execute resource operations for isolated preview runtimes.

    Runtime modules should not open shared resources directly. They ask this
    dispatcher to execute a registered operation in the caller's runtime scope.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._runtimes: dict[str, RuntimeContext] = {}
        self._operations: dict[str, RuntimeHandler] = {}
        self._tool_boundaries: dict[str, _RuntimeToolBoundary] = {}
        self._storage_providers: dict[tuple[str, str | None, str], StorageProvider] = {}
        self._storage_credentials: Callable[[RuntimeContext, str], object | None] | None = None
        self._local = local()
        self.register_operation(FILESYSTEM_READ_TEXT, self._read_runtime_text)
        self.register_operation(FILESYSTEM_WRITE_TEXT, self._write_runtime_text)
        self.register_operation(TOOL_EXECUTE_OPERATION, self._execute_tool)

    @staticmethod
    def _runtime_path(context: RuntimeContext, value: Any) -> Path:
        """Resolve a relative path without permitting escape from the data root."""

        if not context.root:
            raise RuntimeScopeViolation(f"runtime {context.runtime_id!r} has no filesystem root")
        relative = Path(str(value or ""))
        if not value or relative.is_absolute():
            raise RuntimeScopeViolation("runtime filesystem paths must be relative")

        root = Path(context.root).resolve()
        target = (root / relative).resolve()
        if target == root or root not in target.parents:
            raise RuntimeScopeViolation(
                f"runtime {context.runtime_id!r} cannot access a path outside its root"
            )
        return target

    def _read_runtime_text(self, context: RuntimeContext, payload: Mapping[str, Any]) -> str:
        path = self._runtime_path(context, payload.get("path"))
        return path.read_text(encoding="utf-8")

    def _write_runtime_text(self, context: RuntimeContext, payload: Mapping[str, Any]) -> None:
        path = self._runtime_path(context, payload.get("path"))
        content = payload.get("content")
        if not isinstance(content, str):
            raise TypeError("runtime filesystem content must be text")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

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
            root=str(Path(root).resolve()) if root else None,
        )
        with self._lock:
            self._runtimes[runtime_id] = context
            # A reused id must never inherit capabilities from an earlier runtime.
            self._tool_boundaries.pop(runtime_id, None)
            self._storage_providers = {
                key: provider
                for key, provider in self._storage_providers.items()
                if key[0] != runtime_id
            }
        return context

    def unregister_runtime(self, runtime_id: str) -> None:
        with self._lock:
            self._runtimes.pop(runtime_id, None)
            self._tool_boundaries.pop(runtime_id, None)

    def register_tool_executor(
        self,
        runtime_id: str,
        executor: UniversalToolExecutor,
        *,
        policy: RuntimeToolPolicy | None = None,
        approval: RuntimeToolPolicy | None = None,
    ) -> None:
        """Bind one policy-controlled tool executor to an existing runtime.

        Runtime code receives only the ``tools.execute`` dispatcher operation;
        the registry, MCP connectors, and credentials owned by the executor stay
        behind this boundary.
        """
        self.context(runtime_id)
        if not isinstance(executor, UniversalToolExecutor):
            raise TypeError("executor must be a UniversalToolExecutor")
        if policy is not None and not callable(policy):
            raise TypeError("tool policy must be callable")
        if approval is not None and not callable(approval):
            raise TypeError("tool approval must be callable")
        with self._lock:
            self._tool_boundaries[runtime_id] = _RuntimeToolBoundary(
                executor=executor,
                policy=policy,
                approval=approval,
            )

    def register_operation(self, name: str, handler: RuntimeHandler) -> None:
        name = str(name or "").strip()
        if not name:
            raise ValueError("operation name is required")
        if not callable(handler):
            raise TypeError("operation handler must be callable")
        with self._lock:
            self._operations[name] = handler

    def set_storage_credential_resolver(
        self, resolver: Callable[[RuntimeContext, str], object | None] | None
    ) -> None:
        self._storage_credentials = resolver

    def register_storage_provider(
        self, runtime_id: str, provider: StorageProvider, *, owner_id: str | None = None
    ) -> None:
        context = self.context(runtime_id)
        scoped_owner = context.owner_id if owner_id is None else owner_id
        if scoped_owner != context.owner_id:
            raise RuntimeScopeViolation("Storage provider owner does not match runtime owner")
        with self._lock:
            self._storage_providers[(runtime_id, scoped_owner, provider.name)] = provider

    def dispatch_storage(
        self,
        runtime_id: str,
        provider_name: str,
        action: str,
        payload: Mapping[str, Any] | None = None,
        *,
        resource_runtime_id: str | None = None,
    ) -> Any:
        context = self.context(runtime_id)
        if (resource_runtime_id or runtime_id) != runtime_id:
            raise RuntimeScopeViolation("Storage resources cannot cross runtime scopes")
        with self._lock:
            provider = self._storage_providers.get((runtime_id, context.owner_id, provider_name))
        if provider is None:
            raise StorageProviderUnavailable("Storage provider is not configured")
        credentials = (
            self._storage_credentials(context, provider_name) if self._storage_credentials else None
        )
        if provider.requires_credentials and credentials is None:
            raise StorageCredentialsMissing("Storage provider credentials are not configured")
        values = dict(payload or {})
        try:
            if action == "upload":
                return provider.upload(
                    values["object_id"], values["content"], credentials=credentials
                )
            if action == "download":
                return provider.download(values["object_id"], credentials=credentials)
            if action == "list":
                return provider.list(values.get("prefix", ""), credentials=credentials)
            raise StorageError("Unsupported storage operation")
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError("Storage provider operation failed") from exc

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
        caller_owner_id: str | None = None,
    ) -> Any:
        context = self.context(runtime_id)
        if caller_owner_id is not None and context.owner_id != caller_owner_id:
            raise RuntimeScopeViolation("runtime owner does not match the authenticated caller")
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

    def _execute_tool(self, context: RuntimeContext, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            boundary = self._tool_boundaries.get(context.runtime_id)
        if boundary is None:
            raise RuntimeOperationNotFound(
                f"{TOOL_EXECUTE_OPERATION} is not configured for {context.runtime_id!r}"
            )

        raw_call = payload.get("call")
        if isinstance(raw_call, UniversalToolCall):
            call = raw_call
        elif isinstance(raw_call, Mapping):
            call = UniversalToolCall(
                tool_name=str(raw_call.get("tool_name") or raw_call.get("name") or ""),
                arguments=dict(raw_call.get("arguments") or {}),
                call_id=raw_call.get("call_id"),
                transport=str(raw_call.get("transport") or "internal"),
                trace_id=raw_call.get("trace_id"),
                invocation_id=raw_call.get("invocation_id"),
                user_id=raw_call.get("user_id"),
                approved=bool(raw_call.get("approved")),
                metadata=dict(raw_call.get("metadata") or {}),
            )
        else:
            raise TypeError("tools.execute requires a call mapping")

        if context.owner_id is not None and call.user_id not in {None, context.owner_id}:
            raise RuntimeScopeViolation(
                f"runtime {context.runtime_id!r} cannot execute tools as another owner"
            )
        call = replace(
            call,
            user_id=context.owner_id or call.user_id,
            metadata={
                **dict(call.metadata),
                "runtime_id": context.runtime_id,
                "runtime_namespace": context.namespace,
            },
        )

        def apply_policy(definition, tool_call):
            if boundary.policy is None:
                return True
            return boundary.policy(context, definition, tool_call)

        def apply_approval(definition, tool_call):
            if boundary.approval is None:
                return False
            return boundary.approval(context, definition, tool_call)

        return boundary.executor.execute(
            call,
            policy=apply_policy,
            approval=apply_approval,
        )
