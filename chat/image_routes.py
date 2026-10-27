"""Generate one Alice AI ART picture per durable, conversation-bound request."""

import hashlib
import json
import re
import time

import requests
from flask import Blueprint, jsonify, request, send_file

from chat.mcp_routes import LogosClient
from core.config import Config, IMAGE_MODELS
from conversations.ownership import check_access
from economy.treasury_identity import get_current_owner_id
from storage.db import add_message, get_messages, get_store
from storage.generated_images import image_file_metadata, open_image_file, save_image_file
from yandex_client_modules.image_generation import (
    IMAGE_SIZES,
    MAX_PROMPT_CHARS,
    REQUEST_TIMEOUT_SECONDS,
    ImageGenerationError,
    build_generation_payload,
    generate_image as request_image,
)
from yandex_client_modules.request_mixin import (
    _resolve_global_provider_credential,
    provider_request_headers,
)

image_bp = Blueprint("images", __name__)
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,128}")
CONVERSATION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _headers():
    credential = _resolve_global_provider_credential(LogosClient(Config))
    return credential.project_id, provider_request_headers(credential)


def _request_key(conv_id, request_id):
    return "chat/image_request/" + conv_id + "/" + request_id


def _image_reply(conv_id, request_id):
    return "![Alice AI ART](/api/images/files/" + conv_id + "/" + request_id + ")"


def _saved_reply(conv_id, request_id):
    source = "image:" + request_id
    return next(
        (
            message
            for message in get_messages(conv_id)
            if message["role"] == "assistant" and message.get("source") == source
        ),
        None,
    )


def _request_response(conv_id, request_id, record):
    # The binary artifact and its small receipt survive worker restarts independently.
    if not record.get("image_mime"):
        try:
            mime_type, image_bytes = image_file_metadata(conv_id, request_id)
        except (OSError, ValueError):
            pass
        else:
            with get_store().transaction() as tx:
                current = tx.get(_request_key(conv_id, request_id))
                if current is None or tx.get("chat/conversation/" + conv_id) is None:
                    return jsonify({"error": "conversation_not_found"}), 404
                record = {**current, "status": "image_ready", "image_mime": mime_type, "image_bytes": image_bytes}
                tx.set(_request_key(conv_id, request_id), record)
    saved = _saved_reply(conv_id, request_id)
    if saved is not None or record.get("image_mime"):
        try:
            mime_type, image_bytes = image_file_metadata(conv_id, request_id)
            if mime_type != record.get("image_mime") or image_bytes != record.get("image_bytes"):
                raise ValueError("image metadata mismatch")
        except (OSError, ValueError):
            return jsonify({
                "status": "failed", "error": "image_artifact_unavailable",
                "message": "Сохранённый файл изображения недоступен. Повторная платная генерация не запускалась.",
                "request_id": request_id,
            }), 502
    if saved is None and record.get("image_mime") in {"image/png", "image/jpeg"}:
        add_message(
            conv_id, "assistant", _image_reply(conv_id, request_id), cost=None,
            source="image:" + request_id, deduplicate_source=True,
        )
        saved = _saved_reply(conv_id, request_id)
    if saved is not None:
        if record.get("status") != "done":
            with get_store().transaction() as tx:
                current = tx.get(_request_key(conv_id, request_id))
                if current is None or tx.get("chat/conversation/" + conv_id) is None:
                    return jsonify({"error": "conversation_not_found"}), 404
                tx.set(_request_key(conv_id, request_id), {**current, "status": "done"})
        return jsonify({"status": "done", "reply": saved["text"], "message_id": saved["id"], "request_id": request_id})
    status = record.get("status")
    if status == "pending" and time.time() - record["created_at"] <= REQUEST_TIMEOUT_SECONDS + 30:
        return jsonify({"status": "pending", "request_id": request_id}), 202
    # A lost worker/response must never cause another paid request with the same id.
    return jsonify({
        "status": "failed" if status == "failed" else "unknown",
        "error": record.get("error") or "image_generation_outcome_unknown",
        "message": record.get("message") or "Результат генерации не подтверждён. Повторный платный запрос автоматически не выполняется.",
        "request_id": request_id,
    }), 502


def _fail_request(key, error, message, status="failed"):
    with get_store().transaction() as tx:
        record = tx.get(key)
        if record is not None:
            tx.set(key, {**record, "status": status, "error": error, "message": message})


@image_bp.post("/api/images/generate")
def generate_image():
    owner_id = get_current_owner_id()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON object is required"}), 400
    conv_id = data.get("conversation_id")
    request_id = data.get("request_id")
    model_key = data.get("model")
    prompt = data.get("message")
    size = data.get("size")
    if not isinstance(model_key, str) or model_key not in IMAGE_MODELS:
        return jsonify({"error": "not_an_image_model"}), 400
    if not isinstance(conv_id, str) or not CONVERSATION_ID_PATTERN.fullmatch(conv_id):
        return jsonify({"error": "invalid_conversation_id"}), 400
    if not isinstance(request_id, str) or not REQUEST_ID_PATTERN.fullmatch(request_id):
        return jsonify({"error": "request_id is required"}), 400
    if not isinstance(prompt, str) or not prompt.strip():
        return jsonify({"error": "message is required"}), 400
    prompt = prompt.strip()
    if len(prompt) > MAX_PROMPT_CHARS:
        return jsonify({"error": "prompt_too_long", "limit": MAX_PROMPT_CHARS}), 400
    if size is not None and (not isinstance(size, str) or size not in IMAGE_SIZES):
        return jsonify({"error": "invalid_image_size"}), 400
    if data.get("attachments"):
        return jsonify({"error": "image_model_does_not_take_photos"}), 400
    if not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404

    fingerprint = hashlib.sha256(json.dumps([model_key, prompt, size], ensure_ascii=False).encode()).hexdigest()
    key = _request_key(conv_id, request_id)
    existing = get_store().get(key)
    if existing is not None:
        if existing.get("fingerprint") != fingerprint:
            return jsonify({"error": "request_id_conflict"}), 409
        # Only pending and unknown attempts block a new paid call; a failed one may start again.
        if existing.get("status") != "failed":
            return _request_response(conv_id, request_id, existing)

    try:
        project_id, headers = _headers()
        payload = build_generation_payload(project_id, model_key, prompt, size)
    except Exception as exc:
        return jsonify({"error": "provider_credential_unavailable", "message": type(exc).__name__}), 503

    # Reserve before the non-idempotent provider POST, atomically across concurrent clients.
    with get_store().transaction() as tx:
        existing = tx.get(key)
        if existing is None or (existing.get("status") == "failed" and existing.get("fingerprint") == fingerprint):
            if tx.get("chat/conversation/" + conv_id) is None:
                return jsonify({"error": "conversation_not_found"}), 404
            tx.set(key, {"status": "pending", "created_at": time.time(), "fingerprint": fingerprint, "model": model_key})
            existing = None
    if existing is not None:
        if existing.get("fingerprint") != fingerprint:
            return jsonify({"error": "request_id_conflict"}), 409
        return _request_response(conv_id, request_id, existing)

    try:
        add_message(conv_id, "user", prompt, cost=None, source="image-request:" + request_id, deduplicate_source=True)
        image, mime_type = request_image(headers, payload)
    except requests.RequestException as exc:
        response = getattr(exc, "response", None)
        status = response.status_code if response is not None else None
        # The provider may have accepted the generation even when the HTTP reply was lost.
        message = "Ответ провайдера не получен" if status is None else "Провайдер вернул HTTP " + str(status)
        outcome = "unknown" if status is None else "failed"
        _fail_request(key, "image_generation_unavailable", message, status=outcome)
        return jsonify({"status": outcome, "error": "image_generation_unavailable", "message": message, "request_id": request_id}), 502
    except ImageGenerationError as exc:
        message = str(exc)[:200]
        _fail_request(key, "image_generation_failed", message)
        return jsonify({"error": "image_generation_failed", "message": message, "request_id": request_id}), 502
    except Exception:
        _fail_request(key, "image_generation_storage_failed", "Не удалось сохранить запрос или результат генерации")
        return jsonify({"error": "image_generation_storage_failed", "request_id": request_id}), 503

    try:
        # Files are written only while their conversation still exists. Deletion and this
        # receipt update serialize through the same storage transaction.
        with get_store().transaction() as tx:
            record = tx.get(key)
            if record is None or tx.get("chat/conversation/" + conv_id) is None:
                return jsonify({"error": "conversation_not_found"}), 404
            save_image_file(conv_id, request_id, image)
            record = {**record, "status": "image_ready", "image_mime": mime_type, "image_bytes": len(image)}
            tx.set(key, record)
        return _request_response(conv_id, request_id, record)
    except Exception:
        return jsonify({"error": "image_generation_storage_failed", "request_id": request_id}), 503


@image_bp.get("/api/images/requests/<request_id>")
def read_image_request(request_id):
    owner_id = get_current_owner_id()
    conv_id = request.args.get("conversation_id")
    if not REQUEST_ID_PATTERN.fullmatch(request_id or ""):
        return jsonify({"error": "invalid_request_id"}), 400
    if not isinstance(conv_id, str) or not CONVERSATION_ID_PATTERN.fullmatch(conv_id) or not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    record = get_store().get(_request_key(conv_id, request_id))
    if record is None:
        return jsonify({"error": "image_request_not_found"}), 404
    return _request_response(conv_id, request_id, record)


@image_bp.get("/api/images/files/<conv_id>/<request_id>")
def read_generated_image(conv_id, request_id):
    owner_id = get_current_owner_id()
    if not CONVERSATION_ID_PATTERN.fullmatch(conv_id or "") or not REQUEST_ID_PATTERN.fullmatch(request_id or ""):
        return jsonify({"error": "invalid_image_id"}), 400
    if not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    record = get_store().get(_request_key(conv_id, request_id)) or {}
    mime_type = record.get("image_mime")
    if mime_type not in {"image/png", "image/jpeg"}:
        return jsonify({"error": "image_not_found"}), 404
    try:
        image = open_image_file(conv_id, request_id, record.get("image_bytes"))
    except (OSError, ValueError):
        return jsonify({"error": "image_not_found"}), 404
    try:
        response = send_file(image, mimetype=mime_type, download_name=request_id + (".png" if mime_type == "image/png" else ".jpg"), conditional=False, etag=False, max_age=0)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.call_on_close(image.close)
        return response
    except Exception:
        image.close()
        raise
