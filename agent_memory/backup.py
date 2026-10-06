"""Verified local backup/restore bundle for the single-file Memory DB."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from agent_memory.file_memory_db import FileMemoryDB


class MemoryBackupError(Exception):
    """Raised when a backup bundle is incomplete or invalid."""


@dataclass(frozen=True)
class MemoryBackupManifest:
    version: int
    sequence: int
    size: int
    sha256: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "sequence": self.sequence,
                "size": self.size,
                "sha256": self.sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_path(cls, path: Path) -> "MemoryBackupManifest":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            manifest = cls(
                version=raw["version"],
                sequence=raw["sequence"],
                size=raw["size"],
                sha256=raw["sha256"],
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise MemoryBackupError("invalid backup manifest") from exc
        if manifest.version != 1:
            raise MemoryBackupError("unsupported backup version")
        if manifest.sequence < 0 or manifest.size < 0 or len(manifest.sha256) != 64:
            raise MemoryBackupError("invalid backup manifest values")
        return manifest


PAYLOAD_NAME = "alice.memory"
MANIFEST_NAME = "manifest.json"
COMMIT_NAME = "COMMITTED"


def create_backup(db: FileMemoryDB, destination: str | Path) -> MemoryBackupManifest:
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=False)

    with db._lock:
        payload = db.path.read_bytes() if db.path.exists() else b""
        manifest = MemoryBackupManifest(
            version=1,
            sequence=db.sequence,
            size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        _write_fsynced(root / PAYLOAD_NAME, payload)
        _write_fsynced(root / MANIFEST_NAME, (manifest.to_json() + "\n").encode("utf-8"))

    _write_fsynced(root / COMMIT_NAME, b"committed\n")
    _fsync_directory(root)
    return manifest


def restore_backup(source: str | Path, destination: str | Path) -> FileMemoryDB:
    root = Path(source)
    marker = root / COMMIT_NAME
    if not marker.is_file():
        raise MemoryBackupError("backup is not committed")

    manifest = MemoryBackupManifest.from_path(root / MANIFEST_NAME)
    payload_path = root / PAYLOAD_NAME
    try:
        payload = payload_path.read_bytes()
    except OSError as exc:
        raise MemoryBackupError("backup payload is missing") from exc

    if len(payload) != manifest.size:
        raise MemoryBackupError("backup payload size mismatch")
    if hashlib.sha256(payload).hexdigest() != manifest.sha256:
        raise MemoryBackupError("backup payload checksum mismatch")

    target = Path(destination)
    if target.exists():
        raise MemoryBackupError("restore destination already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.restore.tmp")
    try:
        _write_fsynced(temporary, payload)
        candidate = FileMemoryDB(temporary)
        if candidate.sequence != manifest.sequence:
            raise MemoryBackupError("restored sequence mismatch")
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return FileMemoryDB(target)


def copy_verified_backup(source: str | Path, destination: str | Path) -> None:
    """Copy a complete verified bundle for a transport layer to upload."""
    root = Path(source)
    restore_probe = root.with_name(f"{root.name}.verify-probe")
    try:
        restore_backup(root, restore_probe)
    finally:
        restore_probe.unlink(missing_ok=True)
    shutil.copytree(root, destination)


def _write_fsynced(path: Path, payload: bytes) -> None:
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        FileMemoryDB._write_all(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


__all__ = [
    "MemoryBackupError",
    "MemoryBackupManifest",
    "copy_verified_backup",
    "create_backup",
    "restore_backup",
]
