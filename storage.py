"""Provider-neutral storage contracts and the repository-local adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Protocol


class StorageError(RuntimeError):
    """A safe, client-visible storage failure."""


class StorageProviderUnavailable(StorageError):
    pass


class StorageCredentialsMissing(StorageError):
    pass


class StorageObjectNotFound(StorageError):
    pass


@dataclass(frozen=True)
class StorageObject:
    provider: str
    object_id: str
    name: str
    size: int
    location: str
    metadata: Mapping[str, str] | None = None


class StorageProvider(Protocol):
    """Minimal provider interface; provider SDK details remain behind this boundary."""

    name: str
    requires_credentials: bool

    def upload(
        self, object_id: str, content: bytes, *, credentials: object | None
    ) -> StorageObject: ...
    def download(self, object_id: str, *, credentials: object | None) -> bytes: ...
    def list(self, prefix: str, *, credentials: object | None) -> list[StorageObject]: ...


def _safe_relative_path(value: str, *, allow_empty: bool = False) -> PurePosixPath:
    value = str(value or "").replace("\\", "/")
    if value.startswith("/"):
        raise StorageError("Invalid storage object name")
    value = value.strip("/")
    if not value and allow_empty:
        return PurePosixPath(".")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise StorageError("Invalid storage object name")
    return path


class LocalDirectoryStorage:
    """Sandboxed local adapter used for explicit ``local:`` destinations."""

    name = "local"
    requires_credentials = False

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, object_id: str) -> tuple[PurePosixPath, Path]:
        relative = _safe_relative_path(object_id)
        path = (self._root / Path(*relative.parts)).resolve()
        if self._root not in path.parents:
            raise StorageError("Invalid storage object name")
        return relative, path

    def _object(self, relative: PurePosixPath, path: Path) -> StorageObject:
        return StorageObject(
            provider=self.name,
            object_id=relative.as_posix(),
            name=relative.name,
            size=path.stat().st_size,
            location=f"local:{relative.as_posix()}",
        )

    def upload(
        self, object_id: str, content: bytes, *, credentials: object | None = None
    ) -> StorageObject:
        relative, path = self._path(object_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes(content))
        return self._object(relative, path)

    def download(self, object_id: str, *, credentials: object | None = None) -> bytes:
        _, path = self._path(object_id)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise StorageObjectNotFound("Storage object was not found") from exc

    def list(self, prefix: str = "", *, credentials: object | None = None) -> list[StorageObject]:
        relative = _safe_relative_path(prefix, allow_empty=True)
        base = self._root if relative == PurePosixPath(".") else self._path(prefix)[1]
        if not base.exists():
            return []
        files = (
            [base] if base.is_file() else sorted(path for path in base.rglob("*") if path.is_file())
        )
        return [
            self._object(PurePosixPath(path.relative_to(self._root).as_posix()), path)
            for path in files
        ]
