"""Alice AI ART in chat: one generation per message, read through its operation."""

import re
import time
from urllib.parse import quote

import requests
from flask import Blueprint, Response, current_app, jsonify, request

from chat.image_blobs import INLINE_LIMIT_CHARS, load_image, save_image
from chat.mcp_routes import LogosClient
from core.config import Config, IMAGE_MODELS
from conversations.ownership import check_access
from economy.treasury_identity import get_current_owner_id
from storage.db import add_message, get_messages
from yandex_client_modules.image_generation import (
    ImageGenerationError,
    build_generation_payload,
    image_from_operation,
    read_operation,
    start_generation,
)
from yandex_client_modules.request_mixin import (
    _resolve_global_provider_credential,
    provider_request_headers,
)

image_bp = Blueprint("images", __name__)

MAX_PROMPT_CHARS = 1000
LONG_POLL_SECONDS = 25
POLL_INTERVAL_SECONDS = 2
OPERATION_ID_PATTERN = re.compile(r"[A-Za-z0-9_\-]{1,128}")


def _headers():
    credential = _resolve_global_provider_credential(LogosClient(Config))
    return credential.project_id, provider_request_headers(credential)


def _saved_reply(conv_id, operation_id):
    """The assistant message this operation already produced, so repeated polls never save twice."""
    source = "image:" + operation_id
    for message in get_messages(conv_id):
        if message["role"] == "assistant" and message.get("source") == source:
            return message["text"]
    return None


@image_bp.post("/api/images/generate")
def generate_image():
    owner_id = get_current_owner_id()
    data = request.get_json(silent=True) or {}
    conv_id = data.get("conversation_id")
    model_key = data.get("model")
    prompt = str(data.get("message") or "").strip()
    retry = bool(data.get("retry"))
    if model_key not in IMAGE_MODELS:
        return jsonify({"error": "not_an_image_model"}), 400
    if not conv_id or not prompt:
        return jsonify({"error": "conversation_id and message are required"}), 400
    if len(prompt) > MAX_PROMPT_CHARS:
        return jsonify({"error": "prompt_too_long", "limit": MAX_PROMPT_CHARS}), 400
    if data.get("attachments"):
        # Alice AI ART creates pictures from text; a photo sent with it would be dropped silently.
        return jsonify({"error": "image_model_does_not_take_photos"}), 400
    if not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404

    try:
        project_id, headers = _headers()
        operation_id = start_generation(
            headers, build_generation_payload(project_id, model_key, prompt)
        )
    except ImageGenerationError as exc:
        return jsonify({"error": "image_generation_failed", "message": str(exc)[:200]}), 502
    except requests.RequestException as exc:
        response = getattr(exc, "response", None)
        status = response.status_code if response is not None else None
        detail = response.text[:300] if response is not None else type(exc).__name__
        return jsonify({"error": "image_generation_unavailable", "status": status, "message": detail}), 502
    except Exception as exc:
        return jsonify({"error": "provider_credential_unavailable", "message": type(exc).__name__}), 503

    if not retry:
        # A retry reuses the message that is already in the conversation.
        add_message(conv_id, "user", prompt, cost=None)
    return jsonify({"operation_id": operation_id, "model": model_key})


def _image_url(data_url: str, conv_id: str) -> str:
    """Keep a small picture inline; keep a large one as a file that the chat can link to."""
    if len(data_url) <= INLINE_LIMIT_CHARS:
        return data_url
    blob_id = save_image(data_url, conv_id)
    return f"/api/images/blobs/{blob_id}?conversation_id={quote(conv_id)}"


@image_bp.get("/api/images/blobs/<blob_id>")
def read_image_blob(blob_id):
    owner_id = get_current_owner_id()
    conv_id = request.args.get("conversation_id")
    if not conv_id or not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    image = load_image(blob_id, conv_id)
    if image is None:
        return jsonify({"error": "image_not_found"}), 404
    mime, data = image
    return Response(data, mimetype=mime)


@image_bp.get("/api/images/operations/<operation_id>")
def read_image_operation(operation_id):
    owner_id = get_current_owner_id()
    conv_id = request.args.get("conversation_id")
    if not OPERATION_ID_PATTERN.fullmatch(operation_id or ""):
        return jsonify({"error": "invalid_operation_id"}), 400
    if not conv_id or not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404

    saved = _saved_reply(conv_id, operation_id)
    if saved is not None:
        return jsonify({"status": "done", "reply": saved})

    try:
        _, headers = _headers()
        deadline = time.monotonic() + LONG_POLL_SECONDS
        while True:
            data_url = image_from_operation(read_operation(headers, operation_id))
            if data_url:
                break
            if time.monotonic() >= deadline:
                return jsonify({"status": "pending"})
            time.sleep(POLL_INTERVAL_SECONDS)
    except ImageGenerationError as exc:
        return jsonify({"status": "failed", "error": str(exc)[:200]})
    except requests.RequestException as exc:
        return jsonify({"status": "failed", "error": type(exc).__name__})
    except Exception:
        return jsonify({"status": "failed", "error": "provider_credential_unavailable"}), 503

    try:
        reply = "![Alice AI ART](" + _image_url(data_url, conv_id) + ")"
    except (OSError, ValueError):
        current_app.logger.exception("Unable to persist generated image")
        # The provider operation already completed. Keep its id reusable for a later poll;
        # do not start another generation or silently discard the original picture.
        return jsonify({"status": "failed", "error": "image_storage_unavailable"}), 503
    add_message(
        conv_id,
        "assistant",
        reply,
        cost=None,
        model="alice-ai-art",
        source="image:" + operation_id,
    )
    return jsonify({"status": "done", "reply": reply})
