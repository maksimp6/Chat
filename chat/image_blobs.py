"""Pictures too large for a stored message are kept as files and referenced by id."""

from __future__ import annotations
from core.env import env_get

import base64
import hashlib
import mimetypes
import os
import re
import tempfile
import uuid
from pathlib import Path

# Above this size a picture is written to a file; the message keeps only a link to it.
INLINE_LIMIT_CHARS = 256 * 1024
_BLOB_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_SUBTYPE_PATTERN = re.compile(r"[a-z0-9]{2,5}")


def image_dir() -> Path:
    return Path(env_get("LOGOS_IMAGE_DIR", "data/images"))


def _conversation_dir(conversation_id: str) -> Path:
    if not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("conversation_id is required")
    return image_dir() / hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()


def save_image(data_url: str, conversation_id: str) -> str:
    """Write a base64 image data URL to a file and return its blob id."""
    header, _, payload = data_url.partition(",")
    mime = header.removeprefix("data:").split(";", 1)[0]
    subtype = mime.removeprefix("image/")
    if not mime.startswith("image/") or ";base64" not in header or not _SUBTYPE_PATTERN.fullmatch(subtype):
        raise ValueError("unsupported image data URL")
    blob_id = uuid.uuid4().hex
    image_bytes = base64.b64decode(payload, validate=True)
    directory = _conversation_dir(conversation_id)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".image-", dir=directory)
    try:
        # mkstemp creates a private (0600) file; publish only complete image bytes.
        with os.fdopen(descriptor, "wb") as target:
            target.write(image_bytes)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, directory / f"{blob_id}.{subtype}")
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return blob_id


def load_image(blob_id: str, conversation_id: str) -> tuple[str, bytes] | None:
    if not _BLOB_ID_PATTERN.fullmatch(blob_id or ""):
        return None
    for path in _conversation_dir(conversation_id).glob(f"{blob_id}.*"):
        mime = mimetypes.types_map.get(path.suffix, f"image/{path.suffix.removeprefix('.')}")
        return mime, path.read_bytes()
    return None
