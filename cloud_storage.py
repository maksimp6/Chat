"""Provider-neutral, dispatcher-owned artifact storage backends.

Provider credentials are resolved inside this module's dispatcher handler.  Runtime
code only ever names a configured provider and cannot read its credentials.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import Any, Callable, Mapping, Protocol

from runtime.dispatcher import RuntimeContext, RuntimeDispatcher


class StorageError(RuntimeError):
    """Safe, client-visible storage failure."""


class StorageProviderNotConfigured(StorageError):
    pass


class StorageCredentialsMissing(StorageError):
    pass


class StorageObjectNotFound(StorageError):
    pass


class StorageProviderError(StorageError):
    """Sanitized failure from provider code."""


@dataclass(frozen=True)
class StorageObject:
    object_id: str
    name: str
    size: int
    provider: str
    location: str


class StorageProvider(Protocol):
    """Minimal contract shared by local and future cloud adapters."""

    requires_credentials: bool

    def upload(self, name: str, content: bytes, credentials: Any = None) -> StorageObject: ...

    def download(self, object_id: str, credentials: Any = None) -> bytes: ...

    def list(self, prefix: str = "", credentials: Any = None) -> list[StorageObject]: ...


def _safe_relative_name(value: str) -> str:
    path = PurePosixPath(str(value or ""))
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise StorageError("invalid storage object name")
    return path.as_posix()


class LocalDirectoryStorage:
    """Filesystem adapter with explicitly local (not synced/cloud) semantics."""

    requires_credentials = False

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def upload(self, name: str, content: bytes, credentials: Any = None) -> StorageObject:
        object_id = _safe_relative_name(name)
        destination = self.root.joinpath(*PurePosixPath(object_id).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(bytes(content))
        return self._metadata(object_id, destination)

    def download(self, object_id: str, credentials: Any = None) -> bytes:
        object_id = _safe_relative_name(object_id)
        source = self.root.joinpath(*PurePosixPath(object_id).parts)
        if not source.is_file():
            raise StorageObjectNotFound("storage object not found")
        return source.read_bytes()

    def list(self, prefix: str = "", credentials: Any = None) -> list[StorageObject]:
        safe_prefix = "" if not prefix else _safe_relative_name(prefix)
        objects = []
        for path in sorted(item for item in self.root.rglob("*") if item.is_file()):
            object_id = path.relative_to(self.root).as_posix()
            if object_id.startswith(safe_prefix):
                objects.append(self._metadata(object_id, path))
        return objects

    def _metadata(self, object_id: str, path: Path) -> StorageObject:
        return StorageObject(object_id, PurePosixPath(object_id).name, path.stat().st_size, "local", f"local:{object_id}")


ProviderFactory = Callable[[RuntimeContext], StorageProvider]
CredentialResolver = Callable[[RuntimeContext, str], Any]


class ScopedStorageRegistry:
    """Dispatcher-side provider configuration scoped by runtime and owner."""

    def __init__(self, credential_resolver: CredentialResolver | None = None) -> None:
        self._credential_resolver = credential_resolver
        self._providers: dict[tuple[str, str | None, str], ProviderFactory] = {}
        self._lock = RLock()

    def register(self, runtime_id: str, owner_id: str | None, provider: str, factory: ProviderFactory) -> None:
        with self._lock:
            self._providers[(runtime_id, owner_id, provider)] = factory

    def execute(self, context: RuntimeContext, payload: Mapping[str, Any]) -> Any:
        provider_name = str(payload.get("provider") or "")
        with self._lock:
            factory = self._providers.get((context.runtime_id, context.owner_id, provider_name))
        if factory is None:
            raise StorageProviderNotConfigured("storage provider is not configured for this runtime")
        provider = factory(context)
        credentials = None
        if provider.requires_credentials:
            credentials = self._credential_resolver(context, provider_name) if self._credential_resolver else None
            if credentials is None:
                raise StorageCredentialsMissing("storage provider credentials are missing")

        action = payload.get("action")
        try:
            if action == "upload":
                return asdict(provider.upload(str(payload.get("name") or ""), bytes(payload.get("content") or b""), credentials))
            if action == "download":
                return provider.download(str(payload.get("object_id") or ""), credentials)
            if action == "list":
                return [asdict(item) for item in provider.list(str(payload.get("prefix") or ""), credentials)]
            raise StorageError("unsupported storage operation")
        except StorageError:
            raise
        except Exception as exc:
            raise StorageProviderError("storage provider operation failed") from exc


def install_storage_operation(dispatcher: RuntimeDispatcher, registry: ScopedStorageRegistry) -> None:
    """Install the sole runtime entry point for storage resource access."""
    dispatcher.register_operation("storage.execute", registry.execute)
