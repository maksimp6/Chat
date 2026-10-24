"""POST /api/completion: synchronous Yandex completion transport.

The catalog's text_generation_async label groups legacy completion models;
this endpoint requests a synchronous response and does not claim async polling.
"""

from __future__ import annotations

import math

import requests
from flask import Blueprint, jsonify, request

from core.model_profiles import ultra_profiles
from kristall import KristallError
from observability.trace_manager import get_current_trace, traced_operation
from provider_accounts.credentials import CredentialError, YANDEX, get_active_credential
from secret_store.credential_crypto import CredentialCryptoError, decrypt_secret
from storage.db import get_store

completion_bp = Blueprint("completion", __name__)

UPSTREAM_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
UPSTREAM_TIMEOUT_SECONDS = 60
MODEL_KEY = "yandexgpt-5.1"
TRANSPORT = "text_generation_async"
ROLES = {"system", "user", "assistant"}
MAX_MESSAGES = 32
MAX_TEXT_CHARS = 32000


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
            raise _BadInput(f"every message text must be at most {MAX_TEXT_CHARS} characters")
        messages.append({"role": item["role"], "text": text})
    return messages


def _completion_options(body: dict, wire_options: dict) -> dict:
    """Profile defaults, with the caller's temperature and max_tokens checked against them."""
    options = dict(wire_options)
    options["stream"] = False
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
        ceiling = int(wire_options["maxTokens"])
        limit = body["max_tokens"]
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= ceiling:
            raise _BadInput(f"max_tokens must be an integer from 1 to {ceiling}")
        options["maxTokens"] = str(limit)
    return options


@completion_bp.route("/api/completion", methods=["POST"])
@traced_operation("yandex.completion", include_request=False)
def create_completion():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "json_body_required"}), 400
    profile = ultra_profiles().get(MODEL_KEY)
    if profile is None or profile.get("transport") != TRANSPORT:
        return jsonify({"error": "completion_model_unavailable"}), 503
    wire_options = (profile.get("wire_parameters") or {}).get("completionOptions")
    if not isinstance(wire_options, dict) or "maxTokens" not in wire_options:
        return jsonify({"error": "completion_model_unavailable"}), 503
    template = profile.get("model_uri_template")
    if not isinstance(template, str) or not template.startswith("gpt://"):
        return jsonify({"error": "completion_model_unavailable"}), 503
    try:
        messages = _messages_from_body(body)
        options = _completion_options(body, wire_options)
    except _BadInput as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        credential = get_active_credential(get_store(), decrypt_secret, provider=YANDEX)
    except (CredentialError, CredentialCryptoError, KristallError, OSError, ValueError):
        return jsonify({"error": "completion_not_configured"}), 503
    if not credential.api_key or not str(credential.project_id or "").strip():
        return jsonify({"error": "completion_not_configured"}), 503

    model_uri = template.replace("${YANDEX_PROJECT_ID}", credential.project_id)
    payload = {"modelUri": model_uri, "completionOptions": options, "messages": messages}
    try:
        response = requests.post(
            UPSTREAM_URL,
            json=payload,
            headers={"Authorization": f"Api-Key {credential.api_key}"},
            timeout=UPSTREAM_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return jsonify({"error": "completion_upstream_failed", "detail": "Completion provider request failed"}), 502
    if response.status_code != 200:
        detail = f"upstream status {response.status_code}"
        return jsonify({"error": "completion_upstream_failed", "detail": detail}), 502
    try:
        result = response.json()["result"]
        reply = result["alternatives"][0]["message"]["text"]
        usage = result.get("usage")
        if not isinstance(reply, str) or (usage is not None and not isinstance(usage, dict)):
            raise ValueError("invalid completion response")
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return jsonify({"error": "completion_upstream_invalid"}), 502
    trace = get_current_trace()
    if trace is not None:
        trace.add_event("completion_usage", {"model": MODEL_KEY, "usage": usage})
    return jsonify({"model": model_uri, "reply": reply, "usage": usage, "transport": "completion"})
