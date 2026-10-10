"""Immutable owner-scoped 3D scene versions and STL artifacts.

Uses Alice's configured StorageProvider. No caller-provided paths or owners.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from storage import StorageObjectNotFound, StorageProvider

from .engine import export_stl, inspect_scene, new_scene, validate_scene

_REVISION = re.compile(r"[0-9a-f]{64}\Z")


def _canonical(state: dict[str, Any]) -> bytes:
    return json.dumps(
        state, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


class ModelStore:
    """Content-addressed, immutable scene snapshots in an owner namespace."""

    def __init__(self, provider: StorageProvider, owner_id: str, runtime_id: str = "host") -> None:
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("trusted owner identity is required")
        if not isinstance(runtime_id, str) or not runtime_id.strip():
            raise ValueError("runtime scope is required")
        self.provider = provider
        scoped = hashlib.sha256(
            (owner_id.strip() + "\x00" + runtime_id.strip()).encode("utf-8")
        ).hexdigest()
        self.prefix = f"model_editor/{scoped}"

    @staticmethod
    def _model_id(value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("model_id must be a UUID")  # noqa: TRY004 - validation
        try:
            return str(uuid.UUID(value))
        except (ValueError, AttributeError):
            raise ValueError("model_id must be a UUID")

    @staticmethod
    def _revision(value: object) -> str:
        if not isinstance(value, str) or not _REVISION.fullmatch(value):
            raise ValueError("revision must be a sha256 hex string")
        return value

    def _key(self, model_id: str, revision: str) -> str:
        return f"{self.prefix}/{model_id}/revisions/{revision}.json"

    def create(self, name: str = "Untitled model") -> tuple[dict[str, Any], str]:
        state = new_scene(str(uuid.uuid4()), name)
        return state, self.save(state)

    def save(self, state: dict[str, Any]) -> str:
        validate_scene(state)
        model_id = self._model_id(state["model_id"])
        raw = _canonical(state)
        revision = hashlib.sha256(raw).hexdigest()
        key = self._key(model_id, revision)
        # Re-uploading the same content is idempotent. Other revisions are never replaced.
        self.provider.upload(key, raw, credentials=None)
        if self.provider.download(key, credentials=None) != raw:
            raise OSError("model storage verification failed")
        return revision

    def load(self, model_id: object, revision: object) -> dict[str, Any]:
        mid, rev = self._model_id(model_id), self._revision(revision)
        try:
            raw = self.provider.download(self._key(mid, rev), credentials=None)
        except (StorageObjectNotFound, FileNotFoundError) as exc:
            raise ValueError("model or revision not found") from exc
        if hashlib.sha256(raw).hexdigest() != rev:
            raise ValueError("model revision checksum mismatch")
        try:
            parsed: object = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("stored model is not an object")  # noqa: TRY004 - integrity validation
            state: dict[str, Any] = parsed
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("stored model is invalid") from exc
        validate_scene(state)
        if state["model_id"] != mid:
            raise ValueError("stored model identity mismatch")
        return state

    def summary(self, state: dict[str, Any], revision: str) -> dict[str, Any]:
        return {
            **inspect_scene(state),
            "revision": self._revision(revision),
            "parent_revision": state["parent_revision"],
            "scene_storage_ref": self._key(self._model_id(state["model_id"]), revision),
        }

    def export(self, state: dict[str, Any], revision: str) -> dict[str, Any]:
        model_id = self._model_id(state["model_id"])
        rev = self._revision(revision)
        # Only export immutable model versions already persisted in this namespace.
        stored = self.load(model_id, rev)
        raw = export_stl(stored)
        digest = hashlib.sha256(raw).hexdigest()
        key = f"{self.prefix}/{model_id}/exports/{rev}.stl"
        obj = self.provider.upload(key, raw, credentials=None)
        if hashlib.sha256(self.provider.download(key, credentials=None)).hexdigest() != digest:
            raise OSError("STL storage verification failed")
        return {
            "format": "stl",
            "size_bytes": len(raw),
            "sha256": digest,
            "storage_ref": obj.object_id,
            "provider": obj.provider,
        }


__all__ = ["ModelStore"]
