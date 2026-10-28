"""File-backed metadata-only SecretAlias store."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
from pathlib import Path

from secret_store.core import SecretRef
from secret_store.manager import SecretAlias

_THREAD_LOCK = threading.RLock()


@contextlib.contextmanager
def _exclusive(path: Path):
    """Serialize read-modify-write of the alias file across threads and, where fcntl exists, processes."""
    with _THREAD_LOCK:
        try:
            import fcntl
        except ImportError:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(path.with_name(path.name + ".lock"), "a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


class FileSecretAliasStore:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)

    def get(self, alias: str) -> SecretAlias | None:
        return self._load().get(alias)

    def put(self, entry: SecretAlias) -> None:
        with _exclusive(self._path):
            entries = self._load()
            entries[entry.alias] = entry
            self._save(entries)

    def delete(self, alias: str) -> None:
        with _exclusive(self._path):
            entries = self._load()
            entries.pop(alias, None)
            self._save(entries)

    def list(self) -> tuple[SecretAlias, ...]:
        entries = self._load()
        return tuple(entries[key] for key in sorted(entries))

    def _load(self) -> dict[str, SecretAlias]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid secret alias store") from exc
        if (
            not isinstance(raw, dict)
            or set(raw) != {"schema", "aliases"}
            or raw["schema"] != 1
        ):
            raise ValueError("invalid secret alias store")
        aliases = raw["aliases"]
        if not isinstance(aliases, list):
            raise ValueError("invalid secret alias store")
        result: dict[str, SecretAlias] = {}
        for item in aliases:
            if not isinstance(item, dict):
                raise ValueError("invalid secret alias store")
            try:
                ref = SecretRef(
                    provider=item["provider"],
                    secret_id=item["secret_id"],
                    version_id=item["version_id"],
                    purpose=item.get("secret_purpose", ""),
                )
                entry = SecretAlias(
                    alias=item["alias"],
                    ref=ref,
                    allowed_purposes=frozenset(item["allowed_purposes"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid secret alias store") from exc
            if entry.alias in result:
                raise ValueError("invalid secret alias store")
            result[entry.alias] = entry
        return result

    def _save(self, entries: dict[str, SecretAlias]) -> None:
        parent = self._path.parent
        # The directory is created private when it is missing. An existing directory keeps its mode:
        # the alias file may live in a directory the operator shares.
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {
            "schema": 1,
            "aliases": [
                {
                    "alias": entry.alias,
                    "provider": entry.ref.provider,
                    "secret_id": entry.ref.secret_id,
                    "version_id": entry.ref.version_id,
                    "secret_purpose": entry.ref.purpose,
                    "allowed_purposes": sorted(entry.allowed_purposes),
                }
                for entry in sorted(entries.values(), key=lambda item: item.alias)
            ],
        }
        fd, raw_path = tempfile.mkstemp(prefix=".aliases-", dir=parent)
        tmp = Path(raw_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as handle:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._path)
            os.chmod(self._path, 0o600)
        finally:
            tmp.unlink(missing_ok=True)


__all__ = ["FileSecretAliasStore"]
