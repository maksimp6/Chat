"""Alice AI ART in chat: one generation per message, read through its operation."""

import re
import time

import requests
from flask import Blueprint, jsonify, request

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

    reply = "![Alice AI ART](" + data_url + ")"
    add_message(
        conv_id,
        "assistant",
        reply,
        cost=None,
        model="alice-ai-art",
        source="image:" + operation_id,
    )
    return jsonify({"status": "done", "reply": reply})
