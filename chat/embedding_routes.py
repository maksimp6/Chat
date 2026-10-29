"""POST /api/embeddings: the four public Yandex embedding models."""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from chat._error_details import DETAILS, error_body
from chat.host import (
    bound,
    embed_texts,
    embedding_cost,
    get_current_trace,
    traced_operation,
)

embedding_bp = Blueprint("embeddings", __name__)
logger = logging.getLogger("logos.embeddings")

MAX_BATCH = (
    64  # same cap as scripts/rag_pipeline.py: the server key pays for every text
)
# The provider enforces its model-specific token limit. Character counts cannot
# stand in for the provider tokenizer and must not truncate or reject valid text.
ALLOWED_FIELDS = {"text", "texts", "model", "mode", "dimensions"}


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
    return items, single


def _failed(exc: Exception):
    """The response of an embedding call that did not complete (chat.host.EmbeddingError)."""
    code = getattr(exc, "code", "embedding_upstream_failed")
    completed = int(getattr(exc, "completed", 0))
    tokens_spent = int(getattr(exc, "tokens_spent", 0))
    if code == "embeddings_not_configured":
        return jsonify(error_body("embeddings_not_configured")), 503
    if code == "embedding_deadline_exceeded":
        return (
            jsonify(
                error_body(
                    "embedding_deadline_exceeded",
                    completed=completed,
                    tokens_spent=tokens_spent,
                )
            ),
            504,
        )
    detail = DETAILS[
        "embedding_dimensions_mismatch"
        if code == "embedding_dimensions_mismatch"
        else "embedding_upstream_failed"
    ]
    # Texts already embedded were paid for, so the answer reports how far the batch got.
    return (
        jsonify(
            {
                "error": "embedding_upstream_failed",
                "detail": detail,
                "completed": completed,
                "tokens_spent": tokens_spent,
            }
        ),
        502,
    )


@embedding_bp.route("/api/embeddings", methods=["POST"])
@traced_operation("yandex.embeddings", include_request=False)
def create_embeddings():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error_body("json_body_required")), 400
    unknown = set(body) - ALLOWED_FIELDS
    if unknown:
        return jsonify(
            error_body("unknown_parameters", parameters=sorted(unknown))
        ), 400
    if "model" in body and "mode" in body:
        return jsonify(error_body("send model or mode, not both")), 400
    if "model" in body:
        model_name = body["model"]
        if not isinstance(model_name, str) or model_name not in bound.EMBEDDING_MODELS:
            return (
                jsonify(
                    error_body(
                        "unknown embedding model",
                        models=sorted(bound.EMBEDDING_MODELS),
                    )
                ),
                400,
            )
    else:
        mode = body.get("mode", "doc")
        if not isinstance(mode, str) or mode not in bound.EMBEDDING_MODES:
            return jsonify(error_body("mode must be doc or query")), 400
        model_name = bound.EMBEDDING_MODES[mode]
    dimensions = body.get("dimensions")
    if "dimensions" in body:
        if not model_name.startswith("text-embeddings-v2-"):
            return jsonify(error_body("dimensions requires an embedding v2 model")), 400
        if (
            type(dimensions) is not int
            or dimensions not in bound.V2_EMBEDDING_DIMENSIONS
        ):
            return jsonify(
                error_body(
                    "invalid_dimensions",
                    dimensions=sorted(bound.V2_EMBEDDING_DIMENSIONS),
                )
            ), 400
    try:
        items, single = _texts_from_body(body)
    except _BadInput as exc:
        return jsonify(error_body(str(exc), detail_of="invalid_embedding_request")), 400

    try:
        result = embed_texts(
            items,
            model=model_name,
            dimensions=dimensions if "dimensions" in body else None,
            trace=get_current_trace(),
        )
    except bound.EmbeddingError as exc:
        return _failed(exc)
    vectors, total_tokens = result["vectors"], result["tokens"]

    trace = get_current_trace()
    if trace is not None:
        trace.add_event(
            "embedding_usage",
            {
                "model": model_name,
                "texts": len(items),
                "tokens": total_tokens,
            },
        )
    logger.info(
        "embeddings model=%s texts=%d tokens=%d", model_name, len(items), total_tokens
    )
    payload = {
        "model": result["model_uri"],
        "tokens": total_tokens,
        "cost": embedding_cost(total_tokens),
        "dimensions": result["dimensions"],
    }
    if single:
        payload["embedding"] = vectors[0]
    else:
        payload["embeddings"] = vectors
    return jsonify(payload)
