"""In-process registry for embedded external application surfaces."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import hashlib
import hmac
import secrets
import threading
import time
import uuid


MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_INPUT_EVENTS = 256
ALLOWED_FRAME_TYPES = {"image/jpeg", "image/png"}
ALLOWED_INPUT_TYPES = {
    "mouse_move",
    "mouse_down",
    "mouse_up",
    "wheel",
    "key_down",
    "key_up",
    "text",
}


class SurfaceError(RuntimeError):
    """Base error for app-surface operations."""


class SurfaceNotFound(SurfaceError):
    pass


class SurfaceAuthError(SurfaceError):
    pass


class SurfaceValidationError(SurfaceError):
    pass


@dataclass
class SurfaceRecord:
    id: str
    title: str
    source: str
    width: int
    height: int
    producer_token_hash: str
    created_at: float
    updated_at: float
    frame: bytes | None = None
    frame_content_type: str | None = None
    frame_version: int = 0
    inputs: deque[dict] = field(default_factory=lambda: deque(maxlen=MAX_INPUT_EVENTS))

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "source": self.source,
            "width": self.width,
            "height": self.height,
            "frame_version": self.frame_version,
            "has_frame": self.frame is not None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class SurfaceRegistry:
    def __init__(self):
        self._surfaces: dict[str, SurfaceRecord] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def register(self, *, title: str, source: str, width: int, height: int) -> tuple[dict, str]:
        title = str(title or "").strip()
        source = str(source or "").strip()
        if not title:
            raise SurfaceValidationError("title is required")
        if not source:
            raise SurfaceValidationError("source is required")
        if not isinstance(width, int) or not 1 <= width <= 16384:
            raise SurfaceValidationError("width must be an integer between 1 and 16384")
        if not isinstance(height, int) or not 1 <= height <= 16384:
            raise SurfaceValidationError("height must be an integer between 1 and 16384")

        surface_id = uuid.uuid4().hex
        token = secrets.token_urlsafe(32)
        now = time.time()
        record = SurfaceRecord(
            id=surface_id,
            title=title,
            source=source,
            width=width,
            height=height,
            producer_token_hash=self._token_hash(token),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._surfaces[surface_id] = record
        return record.public_dict(), token

    def _get(self, surface_id: str) -> SurfaceRecord:
        with self._lock:
            record = self._surfaces.get(surface_id)
        if record is None:
            raise SurfaceNotFound("surface not found")
        return record

    def authorize_producer(self, surface_id: str, token: str) -> SurfaceRecord:
        record = self._get(surface_id)
        supplied = self._token_hash(str(token or ""))
        if not token or not hmac.compare_digest(record.producer_token_hash, supplied):
            raise SurfaceAuthError("invalid surface producer token")
        return record

    def list(self) -> list[dict]:
        with self._lock:
            return [record.public_dict() for record in self._surfaces.values()]

    def unregister(self, surface_id: str, token: str) -> None:
        self.authorize_producer(surface_id, token)
        with self._lock:
            self._surfaces.pop(surface_id, None)

    def publish_frame(
        self,
        surface_id: str,
        token: str,
        frame: bytes,
        content_type: str,
    ) -> dict:
        if content_type not in ALLOWED_FRAME_TYPES:
            raise SurfaceValidationError("frame content type must be image/jpeg or image/png")
        if not frame:
            raise SurfaceValidationError("frame body is required")
        if len(frame) > MAX_FRAME_BYTES:
            raise SurfaceValidationError("frame exceeds maximum size")

        record = self.authorize_producer(surface_id, token)
        now = time.time()
        with self._lock:
            record.frame = bytes(frame)
            record.frame_content_type = content_type
            record.frame_version += 1
            record.updated_at = now
            return record.public_dict()

    def frame(self, surface_id: str) -> tuple[bytes, str, int]:
        record = self._get(surface_id)
        with self._lock:
            if record.frame is None or record.frame_content_type is None:
                raise SurfaceNotFound("surface frame not available")
            return bytes(record.frame), record.frame_content_type, record.frame_version

    def queue_input(self, surface_id: str, event: dict) -> dict:
        record = self._get(surface_id)
        event_type = str(event.get("type") or "")
        if event_type not in ALLOWED_INPUT_TYPES:
            raise SurfaceValidationError("unsupported input event type")

        normalized = {"type": event_type, "ts": time.time()}
        for key in ("x", "y", "button", "delta_x", "delta_y", "key", "code", "text", "modifiers"):
            if key in event:
                normalized[key] = event[key]

        with self._lock:
            record.inputs.append(normalized)
            record.updated_at = normalized["ts"]
        return normalized

    def drain_inputs(self, surface_id: str, token: str, limit: int = 64) -> list[dict]:
        record = self.authorize_producer(surface_id, token)
        limit = max(1, min(int(limit), MAX_INPUT_EVENTS))
        result = []
        with self._lock:
            while record.inputs and len(result) < limit:
                result.append(record.inputs.popleft())
        return result

    def reset(self) -> None:
        with self._lock:
            self._surfaces.clear()


surface_registry = SurfaceRegistry()
