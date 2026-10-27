"""POST /api/embeddings: the four public Yandex embedding models."""

from __future__ import annotations

from core.wikifunctions import trim_string

import math
import time
from types import SimpleNamespace

import requests
from flask import Blueprint, jsonify, request

from core.config import Config, EMBEDDING_MODELS
from core.env import env_get
from economy.knowledge_economics import KnowledgeCostConfig, calculate_knowledge_cost
from observability import system_log
from observability.trace_manager import get_current_trace, traced_operation
from yandex_client_modules.request_mixin import (
    _resolve_global_provider_credential,
    provider_request_headers,
)

embedding_bp = Blueprint("embeddings", __name__)
logger = system_log.get_logger("embeddings")

UPSTREAM_URL = "https://ai.api.cloud.yandex.net/v1/embeddings"
MAX_BATCH = 64  # same cap as scripts/rag_pipeline.py: the server key pays for every text
# The provider enforces its model-specific token limit. Character counts cannot
# stand in for the provider tokenizer and must not truncate or reject valid text.
UPSTREAM_TIMEOUT_SECONDS = 30
# Whole-request budget, so a batch cannot hold a Flask worker for 30 seconds per text.
REQUEST_BUDGET_SECONDS = 60
MODES = {"doc": "text-search-doc", "query": "text-search-query"}
V2_DIMENSIONS = {128, 256, 512, 768}
ALLOWED_FIELDS = {"text", "texts", "model", "mode", "dimensions"}


class _BadInput(ValueError):
    pass


class _InvalidEmbedding(ValueError):
    def __init__(self, tokens: int):
        super().__init__("invalid embedding response")
        self.tokens = tokens


def _texts_from_body(body: dict) -> tuple[list[str], bool]:
    """Return the input texts and whether the request was a single text."""
    has_text, has_batch = "text" in body, "texts" in body
    if has_text == has_batch:
        raise _BadInput("send exactly one of text or texts")
    if has_text:
        items, single = [body["text"]], True
    else:
        items, single = body["texts"], False
        if not isinstance(items, list) or not items:
            raise _BadInput("texts must be a non-empty list")
        if len(items) > MAX_BATCH:
            raise _BadInput(f"texts accepts at most {MAX_BATCH} items per request")
    for item in items:
        if not isinstance(item, str) or not trim_string(item):
            raise _BadInput("every text must be a non-empty string")
    return items, single


def _token_count(value) -> int:
    """Upstream token counts are integers; a fractional value is truncated, not an error."""
    try:
        return 0 if isinstance(value, bool) else max(0, int(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _embed_one(
    headers: dict[str, str],
    model_uri: str,
    text: str,
    *,
    dimensions: int | None = None,
    timeout: float = UPSTREAM_TIMEOUT_SECONDS,
) -> tuple[list[float], int]:
    payload = {"model": model_uri, "input": text, "encoding_format": "float"}
    if dimensions is not None:
        payload["dimensions"] = dimensions
    response = requests.post(
        UPSTREAM_URL,
        json=payload,
        headers=headers,
        timeout=timeout,
    )
    if response.status_code != 200:
        raise requests.HTTPError(f"upstream status {response.status_code}")
    data = response.json()
    usage = data.get("usage") if isinstance(data, dict) else None
    tokens = _token_count(usage.get("prompt_tokens")) if isinstance(usage, dict) else 0
    records = data.get("data") if isinstance(data, dict) and "error" not in data else None
    record = records[0] if isinstance(records, list) and len(records) == 1 else None
    vector = record.get("embedding") if isinstance(record, dict) else None
    try:
        valid = isinstance(vector, list) and len(vector) == (dimensions or 256) and all(
            type(value) in (int, float) and math.isfinite(value) for value in vector
        )
    except OverflowError:
        valid = False
    if not valid:
        raise _InvalidEmbedding(tokens)
    return vector, tokens


def _cost(tokens: int) -> dict:
    price = env_get("EMBEDDING_PRICE_PER_1K_TOKENS")
    try:
        per_1k_tokens = float(price) if price not in (None, "") else None
    except ValueError:
        per_1k_tokens = None  # a bad price must not turn a paid request into a 500
    if per_1k_tokens is not None and not math.isfinite(per_1k_tokens):
        per_1k_tokens = None  # nan or inf would reach the JSON response unparseable
    config = KnowledgeCostConfig(embedding_per_1k_tokens=per_1k_tokens)
    return calculate_knowledge_cost(embedding_tokens=tokens, config=config)


@embedding_bp.route("/api/embeddings", methods=["POST"])
@traced_operation("yandex.embeddings", include_request=False)
def create_embeddings():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "json_body_required"}), 400
    unknown = set(body) - ALLOWED_FIELDS
    if unknown:
        return jsonify({"error": "unknown_parameters", "parameters": sorted(unknown)}), 400
    if "model" in body and "mode" in body:
        return jsonify({"error": "send model or mode, not both"}), 400
    if "model" in body:
        model_name = body["model"]
        if not isinstance(model_name, str) or model_name not in EMBEDDING_MODELS:
            return (
                jsonify({"error": "unknown embedding model", "models": sorted(EMBEDDING_MODELS)}),
                400,
            )
    else:
        mode = body.get("mode", "doc")
        if not isinstance(mode, str) or mode not in MODES:
            return jsonify({"error": "mode must be doc or query"}), 400
        model_name = MODES[mode]
    dimensions = body.get("dimensions")
    if "dimensions" in body:
        if not model_name.startswith("text-embeddings-v2-"):
            return jsonify({"error": "dimensions requires an embedding v2 model"}), 400
        if type(dimensions) is not int or dimensions not in V2_DIMENSIONS:
            return jsonify({"error": "invalid_dimensions", "dimensions": sorted(V2_DIMENSIONS)}), 400
    try:
        items, single = _texts_from_body(body)
    except _BadInput as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        credential = _resolve_global_provider_credential(
            SimpleNamespace(_config=Config), get_current_trace()
        )
        headers = provider_request_headers(credential)
        headers["x-project"] = credential.project_id
    except Exception:
        return jsonify({"error": "embeddings_not_configured"}), 503
    if not credential.api_key or not trim_string(str(credential.project_id or "")):
        return jsonify({"error": "embeddings_not_configured"}), 503

    model_uri = f"emb://{trim_string(credential.project_id)}/{EMBEDDING_MODELS[model_name]['upstream']}"
    vectors, total_tokens = [], 0
    started = time.monotonic()
    for text in items:
        remaining = REQUEST_BUDGET_SECONDS - (time.monotonic() - started)
        if remaining <= 0:
            return (
                jsonify(
                    {
                        "error": "embedding_deadline_exceeded",
                        "completed": len(vectors),
                        "tokens_spent": total_tokens,
                    }
                ),
                504,
            )
        try:
            vector, tokens = _embed_one(
                headers, model_uri, text,
                dimensions=dimensions,
                timeout=min(UPSTREAM_TIMEOUT_SECONDS, remaining),
            )
        except (requests.RequestException, ValueError) as exc:
            # Texts already embedded were paid for, so report how far the batch got.
            return (
                jsonify(
                    {
                        "error": "embedding_upstream_failed",
                        "detail": "Embedding provider request failed or returned invalid data",
                        "completed": len(vectors),
                        "tokens_spent": total_tokens + getattr(exc, "tokens", 0),
                    }
                ),
                502,
            )
        if vectors and len(vector) != len(vectors[0]):
            return jsonify({
                "error": "embedding_upstream_failed",
                "detail": "Embedding provider returned inconsistent vector dimensions",
                "completed": len(vectors),
                "tokens_spent": total_tokens + tokens,
            }), 502
        vectors.append(vector)
        total_tokens += tokens

    trace = get_current_trace()
    if trace is not None:
        trace.add_event("embedding_usage", {
            "model": model_name, "texts": len(items), "tokens": total_tokens,
        })
    logger.info("embeddings model=%s texts=%d tokens=%d", model_name, len(items), total_tokens)
    payload = {
        "model": model_uri,
        "tokens": total_tokens,
        "cost": _cost(total_tokens),
        "dimensions": dimensions or 256,
    }
    if single:
        payload["embedding"] = vectors[0]
    else:
        payload["embeddings"] = vectors
    return jsonify(payload)
