"""Embeddings from the runner's /api/embed (asmrun serves no embedding model yet: the call fails until it does)."""

from __future__ import annotations

from typing import Any

import requests

from ollama_client import settings
from ollama_client.chat import OllamaError

__all__ = ["embed", "embed_texts"]


def embed(url: str, model: str, texts: list[str], *, timeout: float | None = 300) -> dict[str, Any]:
    """Vectors of ``texts`` by ``model``: {"vectors", "tokens", "model"}."""
    endpoint = url.rstrip("/") + "/api/embed"
    try:
        resp = requests.post(endpoint, json={"model": model, "input": list(texts)}, timeout=timeout)
    except requests.RequestException as exc:
        raise OllamaError(
            f"asmrun {endpoint} unreachable: {type(exc).__name__}: {exc}", reached=False
        ) from exc
    if resp.status_code != 200:
        raise OllamaError(
            f"asmrun embeddings HTTP {resp.status_code}: {resp.text[:500]}", status_code=resp.status_code
        )
    data = resp.json()
    vectors = data.get("embeddings") if isinstance(data, dict) else None
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise OllamaError("asmrun embeddings returned no vector per text")
    return {"vectors": vectors, "tokens": int(data.get("prompt_eval_count") or 0), "model": model}


def embed_texts(texts: list[str], *, model: str, trace: Any = None) -> dict[str, Any]:
    """The vector database's embedding port: ``embed`` on the configured Ollama; ``trace`` stays unwritten."""
    return embed(settings.url(), model, texts)
