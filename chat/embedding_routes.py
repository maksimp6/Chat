"""POST /api/embeddings: Yandex text-search embeddings for the signed-in owner."""

from __future__ import annotations

import requests
from flask import Blueprint, jsonify, request

from core.env import env_get
from economy.knowledge_economics import KnowledgeCostConfig, calculate_knowledge_cost
from economy.treasury_identity import TreasuryIdentityError, get_current_owner_id

embedding_bp = Blueprint("embeddings", __name__)

UPSTREAM_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/textEmbedding"
MAX_BATCH = 64  # same cap as scripts/rag_pipeline.py: the server key pays for every text
# The catalog records two input limits (2048 and 8192 tokens) and does not pick one.
# 4000 characters is a guess that keeps Russian text well under 2048 tokens; not measured.
MAX_TEXT_CHARS = 4000
MODES = {"doc": "text-search-doc", "query": "text-search-query"}
# Embedding models listed in config/model_profiles/yandex-ultra-profiles.json (PR #1572).
EMBEDDING_MODELS = {
    "text-search-doc",
    "text-search-query",
    "text-embeddings-v2-doc",
    "text-embeddings-v2-query",
}
_session = requests.Session()


class _BadInput(ValueError):
    pass


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


def _embed_one(session, key: str, model_uri: str, text: str) -> tuple[list[float], int]:
    response = session.post(
        UPSTREAM_URL,
        json={"modelUri": model_uri, "text": text},
        headers={"Authorization": f"Api-Key {key}"},
        timeout=30,
    )
    if response.status_code != 200:
        raise requests.HTTPError(f"upstream status {response.status_code}")
    data = response.json()
    vector = data.get("embedding")
    if not isinstance(vector, list):
        raise requests.HTTPError("upstream response has no embedding")
    return vector, int(data.get("numTokens") or 0)


def _cost(tokens: int) -> dict:
    price = env_get("EMBEDDING_PRICE_PER_1K_TOKENS")
    config = KnowledgeCostConfig(
        embedding_per_1k_tokens=float(price) if price not in (None, "") else None
    )
    return calculate_knowledge_cost(embedding_tokens=tokens, config=config)


@embedding_bp.route("/api/embeddings", methods=["POST"])
def create_embeddings():
    try:
        owner_id = get_current_owner_id(required=False)
    except TreasuryIdentityError:
        return jsonify({"error": "invalid_owner_token"}), 401
    if not owner_id:
        return jsonify({"error": "owner_identity_required"}), 401

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "json_body_required"}), 400
    if "model" in body:
        model_name = body["model"]
        if not isinstance(model_name, str) or model_name not in EMBEDDING_MODELS:
            return (
                jsonify({"error": "unknown embedding model", "models": sorted(EMBEDDING_MODELS)}),
                400,
            )
    else:
        mode = body.get("mode", "doc")
        if mode not in MODES:
            return jsonify({"error": "mode must be doc or query"}), 400
        model_name = MODES[mode]
    try:
        items, single = _texts_from_body(body)
    except _BadInput as exc:
        return jsonify({"error": str(exc)}), 400

    key = env_get("YANDEX_API_KEY")
    project = env_get("YANDEX_PROJECT_ID")
    if not key or not project:
        return jsonify({"error": "embeddings_not_configured"}), 503

    model_uri = f"emb://{project}/{model_name}"
    vectors, total_tokens = [], 0
    try:
        for text in items:
            vector, tokens = _embed_one(_session, key, model_uri, text)
            vectors.append(vector)
            total_tokens += tokens
    except (requests.RequestException, ValueError) as exc:
        return jsonify({"error": "embedding_upstream_failed", "detail": str(exc)}), 502

    payload = {"model": model_uri, "tokens": total_tokens, "cost": _cost(total_tokens)}
    if single:
        payload["embedding"] = vectors[0]
    else:
        payload["embeddings"] = vectors
    return jsonify(payload)
