"""Collections of texts with their embeddings, kept in the Kristall store and searched by cosine similarity.

One model embeds stored chunks and queries alike: Ollama's ``qwen3-embedding:0.6b`` on
the owner's PC (Logos has no Yandex API since 04.11.2026), at the model's own size.
DOC_MODEL and QUERY_MODEL both name it. A text is split into chunks (paragraphs joined
up to CHUNK_CHARS characters, a longer paragraph cut at that size) kept as
``vector_db/<collection>/<doc id>/<chunk>`` with text, metadata, vector and model.
Chunks embedded earlier by Yandex models are not comparable and are skipped by search.

Kristall is a key/value store with prefix listing and no search of its own: a search
reads every chunk of the asked collections and ranks them by cosine similarity here.
Each embed call returns the tokens spent and writes an event into the caller's
execution trace.
"""

from __future__ import annotations

import hashlib
import math
import time
from typing import Any

from vector_db.host import embed_texts, get_store

__all__ = ["CHUNK_CHARS", "add", "collections", "delete", "drop", "search"]

PREFIX = "vector_db/"
# A chunk the size the provider's search models embed well; the whole text is kept.
CHUNK_CHARS = 2000
# Texts sent in one embed_texts call (its whole-call budget is 60 s).
BATCH = 16
DOC_MODEL = "qwen3-embedding:0.6b"
QUERY_MODEL = DOC_MODEL


def _name(value: str, what: str) -> str:
    value = str(value or "").strip()
    if not value or "/" in value:
        raise ValueError(f"{what} must be a non-empty name without '/'")
    return value


def chunks(text: str) -> list[str]:
    parts, current = [], ""
    for paragraph in [p.strip() for p in text.split("\n\n") if p.strip()]:
        while len(paragraph) > CHUNK_CHARS:
            if current:
                parts.append(current)
                current = ""
            parts.append(paragraph[:CHUNK_CHARS])
            paragraph = paragraph[CHUNK_CHARS:]
        if current and len(current) + 2 + len(paragraph) > CHUNK_CHARS:
            parts.append(current)
            current = ""
        current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        parts.append(current)
    return parts


def _embed(texts: list[str], model: str, trace: Any) -> tuple[list[list[float]], int, str]:
    vectors, tokens = [], 0
    for start in range(0, len(texts), BATCH):
        result = embed_texts(texts[start : start + BATCH], model=model, trace=trace)
        vectors += result["vectors"]
        tokens += int(result.get("tokens") or 0)
    return vectors, tokens, model


def _trace(trace: Any, event: str, payload: dict[str, Any]) -> None:
    if trace is not None and hasattr(trace, "add_event"):
        trace.add_event(event, payload)


def _doc_prefix(collection: str, doc_id: str) -> str:
    return f"{PREFIX}{collection}/{doc_id}/"


def add(
    collection: str, text: str, doc_id: str | None = None, metadata: dict | None = None, trace: Any = None
) -> dict[str, Any]:
    """Embed ``text`` into ``collection``; a document with the same id is replaced."""
    collection = _name(collection, "collection")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    doc_id = _name(doc_id, "id") if doc_id else hashlib.sha256(text.encode()).hexdigest()[:24]
    parts = chunks(text)
    vectors, tokens, model = _embed(parts, DOC_MODEL, trace)
    store = get_store()
    old = store.items(prefix=_doc_prefix(collection, doc_id))
    now = int(time.time())
    # One chunk per transaction: a store journal frame holds at most 1 MiB (kristall).
    for key in old:
        with store.transaction() as tx:
            tx.delete(key)
    for index, (part, vector) in enumerate(zip(parts, vectors, strict=True)):
        with store.transaction() as tx:
            tx.set(
                f"{_doc_prefix(collection, doc_id)}{index:05d}",
                {"collection": collection, "id": doc_id, "chunk": index, "text": part,
                 "metadata": metadata or {}, "vector": vector, "model": model,
                 "dimensions": len(vector), "at": now},
            )
    result = {"collection": collection, "id": doc_id, "chunks": len(parts), "tokens": tokens,
              "model": model, "dimensions": len(vectors[0]) if vectors else 0}
    _trace(trace, "vector_db_add", result)
    return result


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def search(query: str, collection: str | None = None, k: int | None = None, trace: Any = None) -> dict[str, Any]:
    """Chunks ranked by meaning; every collection when none is named, every match when no ``k``."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    prefix = PREFIX + (_name(collection, "collection") + "/" if collection else "")
    (vector,), tokens, model = _embed([query], QUERY_MODEL, trace)
    hits = []
    for record in get_store().items(prefix=prefix).values():
        # Only chunks embedded by the paired document model at the same size are comparable.
        if (
            isinstance(record, dict)
            and record.get("model") == DOC_MODEL
            and isinstance(record.get("vector"), list)
            and len(record["vector"]) == len(vector)
        ):
            hits.append(
                {"score": round(_cosine(vector, record["vector"]), 6),
                 **{key: record.get(key) for key in ("collection", "id", "chunk", "text", "metadata")}}
            )
    hits.sort(key=lambda hit: hit["score"], reverse=True)
    results = hits if k is None else hits[: max(0, int(k))]
    _trace(trace, "vector_db_search", {
        "collection": collection, "searched": len(hits), "results": len(results),
        "top_score": results[0]["score"] if results else None, "tokens": tokens, "model": model,
    })
    return {"results": results, "searched": len(hits), "tokens": tokens, "model": model}


def delete(collection: str, doc_id: str) -> dict[str, Any]:
    store = get_store()
    keys = list(store.items(prefix=_doc_prefix(_name(collection, "collection"), _name(doc_id, "id"))))
    for key in keys:
        with store.transaction() as tx:
            tx.delete(key)
    return {"deleted_chunks": len(keys)}


def drop(collection: str) -> dict[str, Any]:
    store = get_store()
    keys = list(store.items(prefix=PREFIX + _name(collection, "collection") + "/"))
    for key in keys:
        with store.transaction() as tx:
            tx.delete(key)
    return {"deleted_chunks": len(keys)}


def collections() -> dict[str, Any]:
    counts: dict[str, dict[str, Any]] = {}
    for record in get_store().items(prefix=PREFIX).values():
        if isinstance(record, dict):
            entry = counts.setdefault(record.get("collection"), {"documents": set(), "chunks": 0})
            entry["documents"].add(record.get("id"))
            entry["chunks"] += 1
    return {"collections": [
        {"collection": name, "documents": len(entry["documents"]), "chunks": entry["chunks"]}
        for name, entry in sorted(counts.items(), key=lambda item: str(item[0]))
    ]}
