"""POST /api/completion: native synchronous YandexGPT Pro 5.1 completions.

This optional native transport is independent of the model's Responses chat
profile. It never treats the context window as an output-token ceiling.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import requests
from flask import Blueprint, jsonify, request
from chat.host import (
    bound,
    get_current_trace,
    get_model_uri,
    provider_request_headers,
    traced_operation,
)


completion_bp = Blueprint("completion", __name__)

UPSTREAM_URL = "https://ai.api.cloud.yandex.net/foundationModels/v1/completion"
UPSTREAM_TIMEOUT_SECONDS = 60
MODEL_KEY = "yandexgpt-5.1"
ROLES = {"system", "user", "assistant"}
MAX_MESSAGES = 32
MAX_TEXT_CHARS = 32000
MAX_INT64 = (1 << 63) - 1
ALLOWED_FIELDS = {"model", "message", "messages", "temperature", "max_tokens"}


class _BadInput(ValueError):
    pass


def _messages_from_body(body: dict) -> list[dict]:
    if "message" in body and "messages" in body:
        raise _BadInput("send either message or messages")
    if "message" in body:
        raw = [{"role": "user", "text": body["message"]}]
    elif "messages" in body:
        raw = body["messages"]
        if not isinstance(raw, list) or not raw or len(raw) > MAX_MESSAGES:
            raise _BadInput(f"messages must be a list of 1 to {MAX_MESSAGES} items")
    else:
        raise _BadInput("send message or messages")
    messages = []
    for item in raw:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("role"), str)
            or item["role"] not in ROLES
        ):
            raise _BadInput("each message needs role system, user or assistant")
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise _BadInput("every message text must be a non-empty string")
        if len(text) > MAX_TEXT_CHARS:
            raise _BadInput(
                f"every message text must be at most {MAX_TEXT_CHARS} characters"
            )
        messages.append({"role": item["role"], "text": text})
    return messages


def _completion_options(body: dict) -> dict:
    """Use provider defaults unless the caller explicitly supplies an option."""
    options = {"stream": False}
    if "temperature" in body:
        raw_temperature = body["temperature"]
        if isinstance(raw_temperature, bool):
            raise _BadInput("temperature must be a number")
        try:
            temperature = float(raw_temperature)
        except (TypeError, ValueError):
            raise _BadInput("temperature must be a number") from None
        if not math.isfinite(temperature) or not 0 <= temperature <= 1:
            raise _BadInput("temperature must be between 0 and 1")
        options["temperature"] = temperature
    if "max_tokens" in body:
        limit = body["max_tokens"]
        # The API schema is a positive int64. The model validates its own ceiling.
        if type(limit) is not int or not 1 <= limit <= MAX_INT64:
            raise _BadInput("max_tokens must be a positive int64 integer")
        options["maxTokens"] = str(limit)
    return options


@completion_bp.route("/api/completion", methods=["POST"])
@traced_operation("yandex.completion", include_request=False)
def create_completion():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "json_body_required"}), 400
    unknown = set(body) - ALLOWED_FIELDS
    if unknown:
        return jsonify(
            {"error": "unknown_parameters", "parameters": sorted(unknown)}
        ), 400
    if body.get("model", MODEL_KEY) != MODEL_KEY:
        return jsonify(
            {"error": "unsupported_completion_model", "model": MODEL_KEY}
        ), 400
    try:
        messages = _messages_from_body(body)
        options = _completion_options(body)
    except _BadInput as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        credential = bound._resolve_global_provider_credential(
            SimpleNamespace(_config=bound.Config), get_current_trace()
        )
        model_uri = get_model_uri(credential.project_id, MODEL_KEY)
        headers = provider_request_headers(credential)
        headers["x-folder-id"] = credential.project_id
    except Exception:
        return jsonify({"error": "completion_not_configured"}), 503
    if not credential.api_key or not str(credential.project_id or "").strip():
        return jsonify({"error": "completion_not_configured"}), 503

    payload = {
        "modelUri": model_uri,
        "completionOptions": options,
        "messages": messages,
    }
    try:
        response = requests.post(
            UPSTREAM_URL,
            json=payload,
            headers=headers,
            timeout=UPSTREAM_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return jsonify(
            {
                "error": "completion_upstream_failed",
                "detail": "Completion provider request failed",
            }
        ), 502
    if response.status_code != 200:
        detail = f"upstream status {response.status_code}"
        return jsonify({"error": "completion_upstream_failed", "detail": detail}), 502
    try:
        data = response.json()
        if not isinstance(data, dict) or "error" in data:
            raise ValueError("invalid completion response")
        # Current native REST returns this object directly; retain compatibility
        # with the former result envelope without accepting error envelopes.
        result = data.get("result", data)
        if not isinstance(result, dict) or "error" in result:
            raise ValueError("invalid completion response")
        alternative = result["alternatives"][0]
        reply = alternative["message"]["text"]
        status = alternative["status"]
        usage = result.get("usage")
        if (
            not isinstance(reply, str)
            or not isinstance(status, str)
            or (usage is not None and not isinstance(usage, dict))
        ):
            raise ValueError("invalid completion response")
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return jsonify({"error": "completion_upstream_invalid"}), 502
    trace = get_current_trace()
    if trace is not None:
        trace.add_event(
            "completion_usage", {"model": MODEL_KEY, "usage": usage, "status": status}
        )
    if status != "ALTERNATIVE_STATUS_FINAL":
        return jsonify(
            {
                "error": "completion_incomplete",
                "model": model_uri,
                "reply": reply,
                "usage": usage,
                "provider_status": status,
                "transport": "completion",
            }
        ), 502
    return jsonify(
        {
            "model": model_uri,
            "reply": reply,
            "usage": usage,
            "provider_status": status,
            "provider_model_version": result.get("modelVersion"),
            "transport": "completion",
        }
    )
