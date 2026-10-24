"""POST /api/embeddings: Yandex text-search embeddings."""

from __future__ import annotations

import math
import time

import requests
from flask import Blueprint, jsonify, request

from core.env import env_get
from economy.knowledge_economics import KnowledgeCostConfig, calculate_knowledge_cost
from kristall import KristallError
from observability import system_log
from observability.trace_manager import get_current_trace, traced_operation
from provider_accounts.credentials import CredentialError, YANDEX, get_active_credential
from secret_store.credential_crypto import CredentialCryptoError, decrypt_secret
from storage.db import get_store

embedding_bp = Blueprint("embeddings", __name__)
logger = system_log.get_logger("embeddings")

UPSTREAM_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/textEmbedding"
MAX_BATCH = 64  # same cap as scripts/rag_pipeline.py: the server key pays for every text
# The catalog records two input limits (2048 and 8192 tokens) and does not pick one.
# 4000 characters is a guess, not a measured limit.
MAX_TEXT_CHARS = 4000
UPSTREAM_TIMEOUT_SECONDS = 30
# Whole-request budget, so a batch cannot hold a Flask worker for 30 seconds per text.
REQUEST_BUDGET_SECONDS = 60
MODES = {"doc": "text-search-doc", "query": "text-search-query"}
# Embedding models listed in config/model_profiles/yandex-ultra-profiles.json (PR #1572).
EMBEDDING_MODELS = {
    "text-search-doc",
    "text-search-query",
    "text-embeddings-v2-doc",
    "text-embeddings-v2-query",
}


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
        if not isinstance(item, str) or not item.strip():
            raise _BadInput("every text must be a non-empty string")
        if len(item) > MAX_TEXT_CHARS:
            raise _BadInput(f"every text must be at most {MAX_TEXT_CHARS} characters")
    return items, single


def _token_count(value) -> int:
    """Upstream token counts are integers; a fractional value is truncated, not an error."""
    try:
        return 0 if isinstance(value, bool) else max(0, int(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _embed_one(
    key: str, model_uri: str, text: str, *, timeout: float = UPSTREAM_TIMEOUT_SECONDS
) -> tuple[list[float], int]:
    response = requests.post(
        UPSTREAM_URL,
        json={"modelUri": model_uri, "text": text},
        headers={"Authorization": f"Api-Key {key}"},
        timeout=timeout,
    )
    if response.status_code != 200:
        raise requests.HTTPError(f"upstream status {response.status_code}")
    data = response.json()
    vector = data.get("embedding") if isinstance(data, dict) else None
    tokens = _token_count(data.get("numTokens")) if isinstance(data, dict) else 0
    try:
        valid = isinstance(vector, list) and bool(vector) and all(
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
    try:
        items, single = _texts_from_body(body)
    except _BadInput as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        credential = get_active_credential(get_store(), decrypt_secret, provider=YANDEX)
    except (CredentialError, CredentialCryptoError, KristallError, OSError, ValueError):
        return jsonify({"error": "embeddings_not_configured"}), 503
    if not credential.api_key or not str(credential.project_id or "").strip():
        return jsonify({"error": "embeddings_not_configured"}), 503

    model_uri = f"emb://{credential.project_id}/{model_name}"
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
                credential.api_key, model_uri, text,
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
    payload = {"model": model_uri, "tokens": total_tokens, "cost": _cost(total_tokens)}
    if single:
        payload["embedding"] = vectors[0]
    else:
        payload["embeddings"] = vectors
    return jsonify(payload)
