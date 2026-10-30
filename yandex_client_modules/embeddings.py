"""Text embeddings through the Yandex embeddings API, for every unit that indexes by meaning.

``embed_texts`` turns a list of texts into vectors with one of the four public
embedding models (``core.config.EMBEDDING_MODELS``). The paired search models
(``text-search-doc`` for stored texts, ``text-search-query`` for a question) give
vectors that are compared with each other by cosine similarity. The request uses
the deployment-wide provider credential, like every other provider call.

Every text is one paid upstream request. A failure part-way reports how many
texts were embedded and how many tokens were spent, so the caller can account for
what the provider already charged.
"""

from __future__ import annotations

import math
import time
from types import SimpleNamespace
from typing import Any

import requests

from yandex_client_modules.host import bound
from yandex_client_modules.request_mixin import (
    provider_request_headers,
    resolve_global_provider_credential,
)

UPSTREAM_URL = "https://ai.api.cloud.yandex.net/v1/embeddings"
# One upstream request; the provider enforces its own token limit per model.
UPSTREAM_TIMEOUT_SECONDS = 30
# Whole-call budget, so a batch cannot hold a thread for 30 seconds per text.
REQUEST_BUDGET_SECONDS = 60
MODES = {"doc": "text-search-doc", "query": "text-search-query"}
V2_DIMENSIONS = frozenset({128, 256, 512, 768})
DEFAULT_DIMENSIONS = 256


class EmbeddingError(RuntimeError):
    """An embedding call that did not complete; ``code`` names the failure.

    ``completed`` texts were embedded and ``tokens_spent`` were charged before it.
    Codes: ``embeddings_not_configured`` (no usable provider credential),
    ``embedding_upstream_failed`` (the provider refused or answered badly),
    ``embedding_dimensions_mismatch`` (vectors of one batch differ in size) and
    ``embedding_deadline_exceeded`` (the whole-call budget ran out).
    """

    def __init__(
        self, code: str, message: str, *, completed: int = 0, tokens_spent: int = 0
    ) -> None:
        super().__init__(message)
        self.code = code
        self.completed = completed
        self.tokens_spent = tokens_spent


def model_name_for(mode: str | None, model: str | None) -> str:
    """The embedding model key for a request: ``model`` when given, else the mode's pair."""
    if model is not None:
        if not isinstance(model, str) or model not in bound.EMBEDDING_MODELS:
            raise ValueError("unknown embedding model")
        return model
    chosen = "doc" if mode is None else mode
    if not isinstance(chosen, str) or chosen not in MODES:
        raise ValueError("mode must be doc or query")
    return MODES[chosen]


def validate_dimensions(model_name: str, dimensions: Any) -> int | None:
    """``dimensions`` for a v2 model, None when not given; a wrong value raises ValueError."""
    if dimensions is None:
        return None
    if not model_name.startswith("text-embeddings-v2-"):
        raise ValueError("dimensions requires an embedding v2 model")
    if type(dimensions) is not int or dimensions not in V2_DIMENSIONS:
        raise ValueError("invalid_dimensions")
    return dimensions


def _token_count(value: Any) -> int:
    """Upstream token counts are integers; a fractional value is truncated, not an error."""
    try:
        return 0 if isinstance(value, bool) else max(0, int(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


class _InvalidEmbedding(ValueError):
    def __init__(self, tokens: int) -> None:
        super().__init__("invalid embedding response")
        self.tokens = tokens


def embed_one(
    headers: dict[str, str],
    model_uri: str,
    text: str,
    *,
    dimensions: int | None = None,
    timeout: float = UPSTREAM_TIMEOUT_SECONDS,
) -> tuple[list[float], int]:
    """One text's vector and the tokens the provider counted for it."""
    payload: dict[str, Any] = {
        "model": model_uri,
        "input": text,
        "encoding_format": "float",
    }
    if dimensions is not None:
        payload["dimensions"] = dimensions
    response = requests.post(
        UPSTREAM_URL, json=payload, headers=headers, timeout=timeout
    )
    if response.status_code != 200:
        # The provider says why it refused (model URI, dimensions, quota); keep that text.
        detail = response.text[:300]
        raise requests.HTTPError(f"upstream status {response.status_code}: {detail}")
    data = response.json()
    usage = data.get("usage") if isinstance(data, dict) else None
    tokens = _token_count(usage.get("prompt_tokens")) if isinstance(usage, dict) else 0
    records = (
        data.get("data") if isinstance(data, dict) and "error" not in data else None
    )
    record = records[0] if isinstance(records, list) and len(records) == 1 else None
    vector = record.get("embedding") if isinstance(record, dict) else None
    try:
        valid = (
            isinstance(vector, list)
            and len(vector) == (dimensions or DEFAULT_DIMENSIONS)
            and all(
                type(value) in (int, float) and math.isfinite(value) for value in vector
            )
        )
    except OverflowError:
        valid = False
    if not valid:
        raise _InvalidEmbedding(tokens)
    return vector, tokens


def provider_headers(trace: Any = None) -> tuple[str, dict[str, str]]:
    """The project id and request headers of the deployment-wide credential."""
    try:
        credential = resolve_global_provider_credential(
            SimpleNamespace(_config=bound.Config), trace
        )
    except Exception as exc:
        raise EmbeddingError(
            "embeddings_not_configured", "the provider credential is unavailable"
        ) from exc
    project_id = str(credential.project_id or "").strip()
    if not credential.api_key or not project_id:
        raise EmbeddingError(
            "embeddings_not_configured", "the provider credential is incomplete"
        )
    headers = provider_request_headers(credential)
    headers["x-project"] = project_id
    return project_id, headers


def embed_texts(
    texts: list[str],
    *,
    mode: str | None = None,
    model: str | None = None,
    dimensions: int | None = None,
    trace: Any = None,
) -> dict[str, Any]:
    """Vectors of ``texts`` from one embedding model.

    ``model`` names a key of ``EMBEDDING_MODELS``; without it the ``mode`` picks the
    search pair (``doc`` for texts to store, ``query`` for a question; ``doc`` when
    neither is given). Returns ``{"model", "model_uri", "dimensions", "vectors",
    "tokens"}``. A bad argument raises ValueError; a call that does not complete
    raises EmbeddingError with how far it got.
    """
    if not isinstance(texts, list) or not texts:
        raise ValueError("texts must be a non-empty list")
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("every text must be a non-empty string")
    model_name = model_name_for(mode, model)
    size = validate_dimensions(model_name, dimensions)
    project_id, headers = provider_headers(trace)
    model_uri = f"emb://{project_id}/{bound.EMBEDDING_MODELS[model_name]['upstream']}"
    vectors: list[list[float]] = []
    total_tokens = 0
    started = time.monotonic()
    for text in texts:
        remaining = REQUEST_BUDGET_SECONDS - (time.monotonic() - started)
        if remaining <= 0:
            raise EmbeddingError(
                "embedding_deadline_exceeded",
                "the embedding call ran out of time",
                completed=len(vectors),
                tokens_spent=total_tokens,
            )
        try:
            vector, tokens = embed_one(
                headers,
                model_uri,
                text,
                dimensions=size,
                timeout=min(UPSTREAM_TIMEOUT_SECONDS, remaining),
            )
        except (requests.RequestException, ValueError) as exc:
            # Texts already embedded were paid for, so the error says how far it got.
            raise EmbeddingError(
                "embedding_upstream_failed",
                f"the embedding provider did not answer this text: {exc}",
                completed=len(vectors),
                tokens_spent=total_tokens + getattr(exc, "tokens", 0),
            ) from exc
        if vectors and len(vector) != len(vectors[0]):
            raise EmbeddingError(
                "embedding_dimensions_mismatch",
                "the provider returned vectors of different sizes",
                completed=len(vectors),
                tokens_spent=total_tokens + tokens,
            )
        vectors.append(vector)
        total_tokens += tokens
    return {
        "model": model_name,
        "model_uri": model_uri,
        "dimensions": size or DEFAULT_DIMENSIONS,
        "vectors": vectors,
        "tokens": total_tokens,
    }


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Cosine of two vectors; 0.0 when either has no length or the sizes differ."""
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


__all__ = [
    "DEFAULT_DIMENSIONS",
    "MODES",
    "REQUEST_BUDGET_SECONDS",
    "UPSTREAM_TIMEOUT_SECONDS",
    "UPSTREAM_URL",
    "V2_DIMENSIONS",
    "EmbeddingError",
    "cosine_similarity",
    "embed_one",
    "embed_texts",
    "model_name_for",
    "provider_headers",
    "validate_dimensions",
]
