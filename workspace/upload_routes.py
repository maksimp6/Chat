"""Uploads that survive a broken connection: a file or a photo sent in pieces.

On a 10 kb/s link (1.25 KB/s) one request cannot carry a file: every drop starts it
over. Here the browser sends the file in pieces at an offset, asks how much arrived
after a drop and goes on from there.

- ``POST /api/uploads`` ``{name, size, kind: "file" | "image"[, purpose][, id]}``:
  ``201 {id, name, kind, received: 0, size}``. An ``id`` chosen by the browser (32 hex
  digits) makes a repeated create, whose first answer was lost, answer the same upload.
- ``PUT /api/uploads/<id>`` with ``Upload-Offset: <received>`` and the next piece as
  the body: ``{id, name, kind, received, size}``; another offset: ``409 offset_mismatch``
  with the received size.
- ``GET /api/uploads/<id>``: how much arrived. ``DELETE /api/uploads/<id>``: dropped.
- ``POST /api/uploads/<id>/complete``: a file goes to Files as ``POST /api/files`` sends
  it and the answer is the same; an image answers ``{upload_id, size}`` for the
  attachments of ``POST /api/chat``. A repeated completion answers the same again. A
  file is never sent to Files twice: when Files did not answer in time (it may have
  stored the file), or the process stopped while sending, the completion answers
  ``409 completion_uncertain``.

A piece is written to a file of its own first and appended only once it arrived
whole and its offset is still the received size: a piece cut off by a broken
connection, or sent twice, changes nothing.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
import zlib
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

from workspace.file_routes import (
    ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE,
    _err_response,
    get_client,
)
from workspace.host import bound

logger = logging.getLogger("logos.workspace.upload_routes")
upload_bp = Blueprint("uploads", __name__)

_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_OFFSET_PATTERN = re.compile(r"[0-9]{1,18}")
_KINDS = ("file", "image")
# How much of a piece is read from the connection at a time.
_READ_BYTES = 64 * 1024
_FILES_FAILED = "Внутренняя ошибка сервиса файлов"
# Pieces older than this process belong to requests that died with an earlier one.
_BOOTED_AT = time.time()

# A fixed set of locks shared by hash, so their number does not grow with the uploads.
_LOCKS = tuple(threading.Lock() for _ in range(64))


def upload_dir() -> Path:
    return Path(os.environ.get("LOGOS_UPLOAD_DIR", "data/uploads"))


def _paths(upload_id: str) -> tuple[Path, Path]:
    base = upload_dir()
    return base / f"{upload_id}.json", base / f"{upload_id}.part"


def _lock(upload_id: str) -> threading.Lock:
    return _LOCKS[zlib.crc32(upload_id.encode("ascii")) % len(_LOCKS)]


def _storage_failed(error: OSError):
    # A full or read-only disk does not pass by itself: the browser stops instead of trying again.
    logger.error("[UPLOADS] Storage failed: %s", error)
    return jsonify({"error": "upload_storage_failed"}), 507


def _drop_stale_pieces() -> None:
    """Pieces left by requests of an earlier process (a restart in the middle of a piece)."""
    try:
        for piece in upload_dir().glob("*.piece"):
            try:
                if piece.stat().st_mtime < _BOOTED_AT:
                    piece.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _load(upload_id: Any) -> dict[str, Any] | None:
    """The upload's record with ``received`` read from its data, or None when there is none."""
    if not isinstance(upload_id, str) or not _ID_PATTERN.fullmatch(upload_id):
        return None
    meta_path, part_path = _paths(upload_id)
    try:
        meta = json.loads(meta_path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    if meta.get("completed") is not None:
        meta["received"] = meta["size"]
    else:
        meta["received"] = part_path.stat().st_size if part_path.exists() else 0
    return meta


def _state(upload_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": upload_id,
        "name": meta["name"],
        "kind": meta["kind"],
        "received": meta["received"],
        "size": meta["size"],
    }


def _write_meta(upload_id: str, meta: dict[str, Any]) -> None:
    meta_path, _ = _paths(upload_id)
    stored = {key: value for key, value in meta.items() if key != "received"}
    temporary = meta_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(stored, ensure_ascii=False), "utf-8")
    os.replace(temporary, meta_path)


def _remove(upload_id: str) -> None:
    for path in _paths(upload_id):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _not_found():
    return jsonify({"error": "upload_not_found"}), 404


@upload_bp.route("/api/uploads", methods=["POST"])
def create_upload():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object is required"}), 400
    name = data.get("name")
    size = data.get("size")
    kind = data.get("kind")
    purpose = data.get("purpose", "assistants")
    if kind not in _KINDS:
        return jsonify({"error": "kind must be file or image"}), 400
    if not isinstance(name, str) or not os.path.basename(name).strip():
        return jsonify({"error": "name is required"}), 400
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        return jsonify({"error": "size must be a positive number of bytes"}), 400
    if size > MAX_FILE_SIZE:
        return jsonify({"error": "File too large (max 128 MB)"}), 413
    if not isinstance(purpose, str) or not purpose.strip():
        return jsonify({"error": "purpose must be a string"}), 400
    name = os.path.basename(name)
    if kind == "file" and os.path.splitext(name)[1].lower() not in ALLOWED_EXTENSIONS:
        return jsonify(
            {
                "error": f"File type not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            }
        ), 400
    upload_id = data.get("id", uuid.uuid4().hex)
    if not isinstance(upload_id, str) or not _ID_PATTERN.fullmatch(upload_id):
        return jsonify({"error": "id must be 32 hex digits"}), 400
    meta = {"name": name, "size": size, "kind": kind, "purpose": purpose}
    with _lock(upload_id):
        existing = _load(upload_id)
        if existing is not None:
            # The same create sent again: the upload it made answers.
            if all(existing.get(key) == value for key, value in meta.items()):
                return jsonify(_state(upload_id, existing))
            return jsonify({"error": "upload_id_taken"}), 409
        try:
            upload_dir().mkdir(parents=True, exist_ok=True)
            _drop_stale_pieces()
            _, part_path = _paths(upload_id)
            part_path.touch()
            _write_meta(upload_id, meta)
        except OSError as error:
            return _storage_failed(error)
    return jsonify(_state(upload_id, {**meta, "received": 0})), 201


@upload_bp.route("/api/uploads/<upload_id>", methods=["GET"])
def get_upload(upload_id):
    meta = _load(upload_id)
    if meta is None:
        return _not_found()
    return jsonify(_state(upload_id, meta))


@upload_bp.route("/api/uploads/<upload_id>", methods=["DELETE"])
def delete_upload(upload_id):
    if _load(upload_id) is None:
        return _not_found()
    with _lock(upload_id):
        try:
            _remove(upload_id)
        except OSError as error:
            return _storage_failed(error)
    return jsonify({"id": upload_id, "deleted": True})


@upload_bp.route("/api/uploads/<upload_id>", methods=["PUT"])
def append_upload(upload_id):
    meta = _load(upload_id)
    if meta is None:
        return _not_found()
    raw_offset = (request.headers.get("Upload-Offset") or "").strip()
    if not _OFFSET_PATTERN.fullmatch(raw_offset):
        return jsonify({"error": "Upload-Offset must be a byte offset"}), 400
    offset = int(raw_offset)
    # A piece for another place is refused before it is read: the browser asks again from the received size.
    if offset != meta["received"] or meta.get("completed") is not None:
        return jsonify(
            {"error": "offset_mismatch", **_state(upload_id, meta)}
        ), 409
    remaining = meta["size"] - offset
    expected = request.content_length
    if expected is not None and expected > remaining:
        return jsonify({"error": "piece_exceeds_size", **_state(upload_id, meta)}), 413
    _, part_path = _paths(upload_id)
    try:
        handle, piece_name = tempfile.mkstemp(
            prefix=upload_id + ".", suffix=".piece", dir=upload_dir()
        )
    except OSError as error:
        return _storage_failed(error)
    try:
        written = 0
        with os.fdopen(handle, "wb") as piece:
            while True:
                try:
                    block = request.stream.read(_READ_BYTES)
                except Exception:
                    # The connection broke inside the piece: nothing of it is kept.
                    return jsonify(
                        {"error": "piece_incomplete", **_state(upload_id, meta)}
                    ), 400
                if not block:
                    break
                written += len(block)
                if written > remaining:
                    return jsonify(
                        {"error": "piece_exceeds_size", **_state(upload_id, meta)}
                    ), 413
                piece.write(block)
        if written == 0 or (expected is not None and written != expected):
            return jsonify({"error": "piece_incomplete", **_state(upload_id, meta)}), 400
        with _lock(upload_id):
            current = _load(upload_id)
            if current is None:
                return _not_found()
            if current["received"] != offset or current.get("completed") is not None:
                # Another copy of this piece got here first.
                return jsonify(
                    {"error": "offset_mismatch", **_state(upload_id, current)}
                ), 409
            with open(piece_name, "rb") as piece, open(part_path, "ab") as part:
                shutil.copyfileobj(piece, part)
            current["received"] = offset + written
        return jsonify(_state(upload_id, current))
    except OSError as error:
        # A full disk while the piece is written or appended; an appended part is still a correct prefix.
        return _storage_failed(error)
    finally:
        try:
            os.unlink(piece_name)
        except OSError:
            pass


def _uncertain(upload_id: str, meta: dict[str, Any]):
    return jsonify({"error": "completion_uncertain", **_state(upload_id, meta)}), 409


@upload_bp.route("/api/uploads/<upload_id>/complete", methods=["POST"])
def complete_upload(upload_id):
    if _load(upload_id) is None:
        return _not_found()
    with _lock(upload_id):
        meta = _load(upload_id)
        if meta is None:
            return _not_found()
        if meta.get("completed") is not None:
            # A repeated completion (its first answer was lost on the way) answers the same.
            return jsonify(meta["completed"]), 201
        # Files may have stored the file already: sending it again would store a second copy.
        if meta.get("uncertain") or meta.get("completing"):
            return _uncertain(upload_id, meta)
        if meta["received"] != meta["size"]:
            return jsonify({"error": "upload_incomplete", **_state(upload_id, meta)}), 409
        if meta["kind"] == "image":
            return jsonify({"upload_id": upload_id, "size": meta["size"]})
        _, part_path = _paths(upload_id)
        try:
            content = part_path.read_bytes()
            # Marked before sending: a process that stops while Files stores the file leaves this mark.
            _write_meta(upload_id, {**meta, "completing": True})
        except OSError as error:
            return _storage_failed(error)
        try:
            client = get_client()
            logger.info(
                "[UPLOADS] Sending '%s', %d bytes, to Files", meta["name"], len(content)
            )
            result = client.upload_file(content, meta["name"], purpose=meta["purpose"])
        except bound.YandexClientError as e:
            if getattr(e, "status_code", None) == 504:
                # No answer in time: the file may be stored. The browser says so instead of sending it again.
                _write_meta(upload_id, {**meta, "uncertain": True})
                return _uncertain(upload_id, meta)
            _write_meta(upload_id, meta)
            return _err_response(e)
        except Exception as e:
            logger.error("[UPLOADS] Completion failed: %s", e, exc_info=True)
            _write_meta(upload_id, {**meta, "uncertain": True})
            return _uncertain(upload_id, meta)
        _write_meta(upload_id, {**meta, "completed": result})
        try:
            part_path.unlink()
        except FileNotFoundError:
            pass
        return jsonify(result), 201


def read_upload(upload_id: Any) -> bytes:
    """The bytes of a complete image upload; ValueError when there is none."""
    meta = _load(upload_id)
    if meta is None or meta["kind"] != "image":
        raise ValueError("unknown image upload")
    if meta["received"] != meta["size"]:
        raise ValueError("image upload is not complete")
    _, part_path = _paths(upload_id)
    return part_path.read_bytes()
