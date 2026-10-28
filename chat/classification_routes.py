"""Prompt classifiers have their own transport; they are not chat models."""

from __future__ import annotations

import math
from types import SimpleNamespace

import requests
from flask import Blueprint, jsonify, request
from chat._error_details import DETAILS, error_body
from chat.host import (
    bound,
    get_current_trace,
    provider_request_headers,
    record_usage,
    release_request,
    reserve_request,
    traced_operation,
)


classification_bp = Blueprint("classification", __name__)

UPSTREAM_URL = (
    "https://ai.api.cloud.yandex.net/foundationModels/v1/fewShotTextClassification"
)
UPSTREAM_TIMEOUT_SECONDS = 60
MAX_LABELS = 20  # Documented limit of the prompt-based classification API.
DEFAULT_MODEL = "classifier-yandexgpt-lite"
REQUEST_FIELDS = {"model", "text", "taskDescription", "labels", "samples"}


class _BadInput(ValueError):
    pass


def _nonempty_string(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _BadInput(f"{field} must be a non-empty string")
    return value


def _classification_payload(body: dict) -> tuple[str, dict]:
    unknown = sorted(set(body) - REQUEST_FIELDS)
    if unknown:
        raise _BadInput("unknown fields: " + ", ".join(unknown))
    model = body.get("model", DEFAULT_MODEL)
    if not isinstance(model, str) or model not in bound.CLASSIFIER_MODELS:
        raise _BadInput("unknown classifier model")
    labels = body.get("labels")
    if not isinstance(labels, list) or not 1 <= len(labels) <= MAX_LABELS:
        raise _BadInput(f"labels must contain from 1 to {MAX_LABELS} strings")
    for label in labels:
        _nonempty_string(label, "each label")
    if len(set(labels)) != len(labels):
        raise _BadInput("labels must be unique")
    payload = {
        "text": _nonempty_string(body.get("text"), "text"),
        "taskDescription": _nonempty_string(
            body.get("taskDescription"), "taskDescription"
        ),
        "labels": labels,
    }
    if "samples" in body:
        samples = body["samples"]
        if not isinstance(samples, list):
            raise _BadInput("samples must be a list")
        for sample in samples:
            if not isinstance(sample, dict) or set(sample) != {"text", "label"}:
                raise _BadInput("each sample must contain text and label")
            _nonempty_string(sample["text"], "sample text")
            _nonempty_string(sample["label"], "sample label")
            if sample["label"] not in labels:
                raise _BadInput("each sample label must be included in labels")
        payload["samples"] = samples
    return model, payload


def _classification_result(data, labels: list[str]) -> dict:
    if not isinstance(data, dict) or "error" in data:
        raise ValueError("invalid classification response")
    predictions = data.get("predictions")
    if not isinstance(predictions, list) or len(predictions) != len(labels):
        raise ValueError("incomplete classification predictions")
    seen = set()
    normalized = []
    for prediction in predictions:
        if not isinstance(prediction, dict):
            raise ValueError("invalid classification prediction")
        label, confidence = prediction.get("label"), prediction.get("confidence")
        if not isinstance(label, str) or label not in labels or label in seen:
            raise ValueError("invalid classification label")
        if isinstance(confidence, bool) or not isinstance(
            confidence, (int, float, str)
        ):
            raise ValueError("invalid classification confidence")
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("invalid classification confidence")
        seen.add(label)
        normalized.append({"label": label, "confidence": confidence})
    result = {"predictions": normalized}
    if "modelVersion" in data:
        if not isinstance(data["modelVersion"], str):
            raise ValueError("invalid classifier model version")
        result["modelVersion"] = data["modelVersion"]
    if "inputTokens" in data:
        raw = data["inputTokens"]
        if isinstance(raw, bool) or not isinstance(raw, (str, int)):
            raise ValueError("invalid classifier input token count")
        tokens = int(raw)
        if tokens < 0:
            raise ValueError("invalid classifier input token count")
        result["inputTokens"] = tokens
    return result


@classification_bp.post("/api/classify")
@traced_operation("yandex.classification", include_request=False)
def classify_text():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error_body("json_body_required")), 400
    try:
        model, payload = _classification_payload(body)
    except _BadInput as exc:
        return jsonify(
            error_body("invalid_classification_request", message=str(exc))
        ), 400

    try:
        credential = bound._resolve_global_provider_credential(
            SimpleNamespace(_config=bound.Config)
        )
        upstream = bound.CLASSIFIER_MODELS[model]["upstream"]
        payload["modelUri"] = f"cls://{credential.project_id}/{upstream}"
        headers = provider_request_headers(credential)
    except Exception:
        # Neither raw exceptions nor upstream bodies are safe credential diagnostics.
        return jsonify(error_body("classification_not_configured")), 503

    try:
        reservation = reserve_request(bound._resolve_quota_user_id())
    except bound.ProviderQuotaExceeded as exc:
        return jsonify(
            {**exc.to_dict(), "detail": DETAILS["provider_quota_exceeded"]}
        ), 429

    try:
        response = requests.post(
            UPSTREAM_URL,
            json=payload,
            headers=headers,
            timeout=UPSTREAM_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        # A timed-out POST can still complete at the provider. Do not retry it or
        # return its admission reservation as though non-execution were proven.
        return jsonify(
            error_body(
                "classification_upstream_failed",
                detail_of="classification_upstream_unknown",
                outcome="unknown",
            )
        ), 502
    if not 200 <= response.status_code < 300:
        if response.status_code in {400, 401, 403, 404, 429}:
            release_request(reservation)
        return jsonify(
            error_body(
                "classification_upstream_failed",
                upstream_status=response.status_code,
            )
        ), 502
    try:
        result = _classification_result(response.json(), payload["labels"])
    except (ValueError, TypeError, OverflowError):
        return jsonify(error_body("classification_upstream_invalid")), 502
    if "inputTokens" in result:
        record_usage(reservation, token_count=result["inputTokens"])
    trace = get_current_trace()
    if trace is not None:
        trace.add_event(
            "classification_usage",
            {
                "model": model,
                "labels": len(payload["labels"]),
                "samples": len(payload.get("samples", [])),
                "input_tokens": result.get("inputTokens"),
                "model_version": result.get("modelVersion"),
            },
        )
    return jsonify({"model": payload["modelUri"], **result})
