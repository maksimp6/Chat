"""The Logos cache: one long text that only grows (category ``cache``).

``CACHE_TOOLS`` holds three tools: ``cache_append`` adds text to the end,
``cache_read`` reads the text or a part of it and ``cache_search`` finds places in
it by substring, regular expression or meaning. The model and other tools write to
one text shared by every dialog. Nothing in it is ever changed or removed.

Each append is one piece of the text: a heading line (time, source, dialog) and the
appended text. Every piece is embedded with each model of ``EMBEDDING_MODELS`` (v2
models at their largest size); the vectors are kept beside the text with the model
URI each came from, so the version of every embedding model is recorded. A model
that fails is listed in the piece's ``embedding_errors``; the text is appended either
way. A semantic search embeds the question with the query models and compares it
with each piece: a document vector with its paired query model, a query vector with
the same model.

The whole text is in the index: after every append it is uploaded as one file
(``logos-cache.md``) to each knowledge vector store (``knowledge_vector_store_ids``),
the store the dialog's File Search searches, and the previous upload is taken out of
the stores and deleted, so the index holds the current text once. A failure is
returned in ``index_errors``; the append is kept either way.

Files beside the memory file: ``logos.cache.md`` (the text),
``logos.cache.vectors.jsonl`` (one record per piece) and ``logos.cache.index.json``
(the uploaded file). Writers hold an exclusive file lock. There is no limit on the
size of the text (0 means no application limit).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import re
import threading
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tool_providers.host import (
    bound,
    cosine_similarity,
    embed_texts,
    knowledge_vector_store_ids,
    memory_file_path,
)
from tool_providers.schema import object_schema

CACHE_FILE_NAME = "logos.cache.md"
VECTORS_FILE_NAME = "logos.cache.vectors.jsonl"
INDEX_STATE_FILE_NAME = "logos.cache.index.json"
INDEX_FILE_NAME = "logos-cache.md"
SEARCH_MODES = ("all", "text", "regex", "semantic")
_TRANSPORTS = ["responses_api", "local_agent", "mcp"]
_text_lock = threading.Lock()
_index_lock = threading.Lock()


def cache_file_path() -> Path:
    """The cache text: ``logos.cache.md`` beside the memory file."""
    return memory_file_path().with_name(CACHE_FILE_NAME)


def _vectors_path() -> Path:
    return memory_file_path().with_name(VECTORS_FILE_NAME)


def _index_state_path() -> Path:
    return memory_file_path().with_name(INDEX_STATE_FILE_NAME)


@contextlib.contextmanager
def _locked(lock: threading.Lock, path: Path) -> Iterator[None]:
    """Hold ``lock`` in this process and an exclusive lock on ``path`` + ``.lock``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock, path.with_name(path.name + ".lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _read_text() -> str:
    path = cache_file_path()
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _read_pieces() -> list[dict[str, Any]]:
    path = _vectors_path()
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# ---------------------------------------------------------------------------
# Caller context


def _call_of(cfg: Mapping[str, Any] | None) -> Any:
    context = cfg.get("_universal_context") if isinstance(cfg, Mapping) else None
    return context.get("call") if isinstance(context, Mapping) else None


def _trace_of(cfg: Mapping[str, Any] | None) -> Any:
    metadata = getattr(_call_of(cfg), "metadata", None)
    return metadata.get("execution_trace") if isinstance(metadata, Mapping) else None


def _conversation_of(cfg: Mapping[str, Any] | None) -> str | None:
    data = getattr(_trace_of(cfg), "trace", None)
    context = data.get("context") if isinstance(data, Mapping) else None
    conversation = (
        context.get("conversation_id") if isinstance(context, Mapping) else None
    )
    return str(conversation or "") or None


def _failure(message: str, phase: str, **extra: Any) -> dict[str, Any]:
    return {"success": False, "error": message, "metadata": {"phase": phase, **extra}}


# ---------------------------------------------------------------------------
# Embeddings


def _embedding_models() -> dict[str, dict[str, Any]]:
    return dict(bound.EMBEDDING_MODELS)


def _largest_dimensions(name: str, spec: Mapping[str, Any]) -> int | None:
    """v2 models take the largest size they offer; the search pair has one fixed size."""
    if not name.startswith("text-embeddings-v2-"):
        return None
    sizes = [size for size in spec.get("dimensions") or () if type(size) is int]
    return max(sizes) if sizes else None


def _query_model_for(name: str) -> str:
    """The model that embeds a question compared with vectors of ``name``."""
    return name[: -len("-doc")] + "-query" if name.endswith("-doc") else name


def _embed(text: str, name: str, spec: Mapping[str, Any], trace: Any) -> dict[str, Any]:
    """One model's vector of ``text`` with its model URI and tokens, or the failure."""
    try:
        result = embed_texts(
            [text],
            model=name,
            dimensions=_largest_dimensions(name, spec),
            trace=trace,
        )
    except bound.EmbeddingError as exc:
        return {"error": exc.code, "message": str(exc), "tokens": exc.tokens_spent}
    except ValueError as exc:
        return {"error": "invalid_embedding_request", "message": str(exc), "tokens": 0}
    return {
        "model_uri": result["model_uri"],
        "upstream": spec.get("upstream"),
        "dimensions": result["dimensions"],
        "vector": result["vectors"][0],
        "tokens": result["tokens"],
    }


# ---------------------------------------------------------------------------
# Index (knowledge vector stores)


def _failure_record(exc: BaseException) -> dict[str, str]:
    return {"error": type(exc).__name__, "message": str(exc)}


def _refresh_index() -> tuple[dict[str, Any], dict[str, Any]]:
    """Put the current text in every knowledge store in place of the previous upload."""
    # Provider errors, credential errors (RuntimeError) and network errors (OSError).
    failures = (bound.YandexClientError, RuntimeError, OSError, ValueError)
    errors: dict[str, Any] = {}
    state_path = _index_state_path()
    with _locked(_index_lock, state_path):
        previous = (
            json.loads(state_path.read_text(encoding="utf-8"))
            if state_path.exists()
            else {}
        )
        text = _read_text()
        try:
            client = bound.YandexResponsesClient(bound.Config)
            uploaded = client.upload_file(
                text.encode("utf-8"), INDEX_FILE_NAME, purpose="assistants"
            )
        except failures as exc:
            errors["upload"] = _failure_record(exc)
            return previous, errors
        file_id = uploaded.get("id") if isinstance(uploaded, Mapping) else None
        if not file_id:
            errors["upload"] = {"error": "no_file_id", "message": str(uploaded)}
            return previous, errors
        state: dict[str, Any] = {
            "file_id": file_id,
            "filename": INDEX_FILE_NAME,
            "chars": len(text),
            "uploaded_at": datetime.now(UTC).isoformat(),
            "vector_stores": {},
        }
        for store_id in knowledge_vector_store_ids():
            try:
                added = client.add_file_to_vs(store_id, file_id)
            except failures as exc:
                errors[store_id] = _failure_record(exc)
                continue
            state["vector_stores"][store_id] = (
                added.get("status") if isinstance(added, Mapping) else None
            )
        old_id = previous.get("file_id")
        if old_id and old_id != file_id:
            # The previous upload holds an older copy of the same text.
            for store_id in previous.get("vector_stores") or {}:
                try:
                    client.remove_file_from_vs(store_id, old_id)
                except failures as exc:
                    errors[f"remove:{store_id}"] = _failure_record(exc)
            try:
                client.delete_file(old_id)
            except failures as exc:
                errors["delete"] = _failure_record(exc)
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        return state, errors


# ---------------------------------------------------------------------------
# Tools


def _piece_public(piece: Mapping[str, Any], text: str | None = None) -> dict[str, Any]:
    """A piece without its vectors; with its text when the whole text is given."""
    result = {key: value for key, value in piece.items() if key != "embeddings"}
    result["embeddings"] = {
        name: {key: value for key, value in record.items() if key != "vector"}
        for name, record in (piece.get("embeddings") or {}).items()
    }
    if text is not None:
        start = piece.get("start") or 0
        result["text"] = text[start : start + (piece.get("chars") or 0)]
    return result


def cache_append(arguments: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    """Append text to the end of the cache, embed it with every model, refresh the index."""
    text = arguments.get("text") if isinstance(arguments, Mapping) else None
    if not isinstance(text, str) or not text.strip():
        return _failure("text must be a non-empty string", "validation")
    source = arguments.get("source")
    source = source.strip() if isinstance(source, str) and source.strip() else "model"

    trace = _trace_of(cfg)
    embeddings: dict[str, Any] = {}
    embedding_errors: dict[str, Any] = {}
    tokens = 0
    for name, spec in _embedding_models().items():
        record = _embed(text, name, spec, trace)
        tokens += record.get("tokens") or 0
        if "error" in record:
            embedding_errors[name] = record
        else:
            embeddings[name] = record

    created_at = datetime.now(UTC).isoformat()
    conversation_id = _conversation_of(cfg)
    heading = " · ".join(part for part in (created_at, source, conversation_id) if part)
    path = cache_file_path()
    with _locked(_text_lock, path):
        existing = _read_text()
        separator = "\n\n" if existing else ""
        piece_text = f"## {heading}\n\n{text}"
        start = len(existing) + len(separator)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(separator + piece_text)
            handle.flush()
        piece = {
            "start": start,
            "chars": len(piece_text),
            "created_at": created_at,
            "source": source,
            "conversation_id": conversation_id,
            "embedding_tokens": tokens,
            "embeddings": embeddings,
            "embedding_errors": embedding_errors,
        }
        with _vectors_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(piece, ensure_ascii=False) + "\n")
        total = start + len(piece_text)

    index, index_errors = _refresh_index()
    return {
        "success": True,
        "path": str(path),
        "total_chars": total,
        "piece": _piece_public(piece),
        "index": index,
        "index_errors": index_errors,
    }


def cache_read(arguments: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    """The cache text, whole or ``length`` characters from ``offset``."""
    arguments = arguments if isinstance(arguments, Mapping) else {}
    offset = arguments.get("offset") or 0
    length = arguments.get("length") or 0
    if type(offset) is not int or type(length) is not int or length < 0:
        return _failure("offset and length must be integers", "validation")
    text = _read_text()
    start = max(0, len(text) + offset) if offset < 0 else min(offset, len(text))
    end = min(start + length, len(text)) if length else len(text)
    return {
        "success": True,
        "path": str(cache_file_path()),
        "total_chars": len(text),
        "start": start,
        "end": end,
        "text": text[start:end],
    }


def _text_hits(text: str, query: str, pattern: re.Pattern[str] | None) -> list[int]:
    if pattern is not None:
        return [match.start() for match in pattern.finditer(text)]
    hits, needle, haystack = [], query.casefold(), text.casefold()
    position = haystack.find(needle)
    while position != -1:
        hits.append(position)
        position = haystack.find(needle, position + 1)
    return hits


def _piece_at(pieces: list[dict[str, Any]], position: int) -> int | None:
    for number, piece in enumerate(pieces):
        start = piece.get("start") or 0
        if start <= position < start + (piece.get("chars") or 0):
            return number
    return None


def cache_search(arguments: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    """Find places in the cache by substring, regular expression or meaning."""
    arguments = arguments if isinstance(arguments, Mapping) else {}
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return _failure("query must be a non-empty string", "validation")
    mode = arguments.get("mode") or "all"
    if mode not in SEARCH_MODES:
        return _failure(f"mode must be one of {', '.join(SEARCH_MODES)}", "validation")
    models = _embedding_models()
    rank_by = arguments.get("rank_by") or "mean"
    if rank_by != "mean" and rank_by not in models:
        return _failure("rank_by must be mean or an embedding model", "validation")
    limit = arguments.get("limit") or 0
    if type(limit) is not int or limit < 0:
        return _failure("limit must be an integer of 0 or more", "validation")
    pattern = None
    if mode == "regex":
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as exc:
            return _failure(f"invalid regular expression: {exc}", "validation")

    text = _read_text()
    pieces = _read_pieces()
    hits = _text_hits(text, query, pattern) if mode != "semantic" else []
    matched = {_piece_at(pieces, position) for position in hits}

    if mode in ("text", "regex"):
        results: list[dict[str, Any]] = []
        for position in hits[:limit] if limit else hits:
            number = _piece_at(pieces, position)
            results.append(
                {
                    "position": position,
                    "piece": None
                    if number is None
                    else _piece_public(pieces[number], text),
                }
            )
        return {
            "success": True,
            "path": str(cache_file_path()),
            "mode": mode,
            "total_chars": len(text),
            "matches": len(hits),
            "results": results,
        }

    trace = _trace_of(cfg)
    query_vectors: dict[str, Any] = {}
    tokens = 0
    for name in dict.fromkeys(_query_model_for(name) for name in models):
        if name not in models:
            continue
        record = _embed(query, name, models[name], trace)
        tokens += record.get("tokens") or 0
        if "error" in record:
            return _failure(
                f"the query was not embedded with {name}: {record['message']}",
                "execution",
                code=record["error"],
                model=name,
                embedding_tokens=tokens,
            )
        query_vectors[name] = record["vector"]

    ranked = []
    for number, piece in enumerate(pieces):
        scores = {}
        for name, record in (piece.get("embeddings") or {}).items():
            vector = query_vectors.get(_query_model_for(name))
            if vector is not None and isinstance(record.get("vector"), list):
                scores[name] = cosine_similarity(vector, record["vector"])
        if rank_by == "mean":
            score = sum(scores.values()) / len(scores) if scores else None
        else:
            score = scores.get(rank_by)
        ranked.append(
            {
                "score": score,
                "scores": scores,
                "text_match": number in matched,
                "piece": _piece_public(piece, text),
            }
        )
    ranked.sort(
        key=lambda item: (
            item["score"] is not None,
            mode == "all" and item["text_match"],
            item["score"] if item["score"] is not None else 0.0,
        ),
        reverse=True,
    )
    if limit:
        ranked = ranked[:limit]
    return {
        "success": True,
        "path": str(cache_file_path()),
        "mode": mode,
        "rank_by": rank_by,
        "total_chars": len(text),
        "matches": len(hits),
        "query_embedding_tokens": tokens,
        "results": ranked,
    }


# ---------------------------------------------------------------------------
# Definitions


def _optional(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _definition(
    *,
    title: str,
    description: str,
    parameters: dict[str, Any],
    read_only: bool,
    func: Any,
) -> dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "parameters": parameters,
        "capabilities": ["cache", "embeddings", "diagnostics"],
        "risk_level": "low",
        "read_only": read_only,
        "requires_approval": False,
        "supported_transports": list(_TRANSPORTS),
        "executor": {"type": "local"},
        "func": func,
    }


CACHE_TOOLS: dict[str, dict[str, Any]] = {
    "cache_append": _definition(
        title="Cache: append",
        description=(
            "Append text to the end of the Logos cache: one long text that only grows "
            "and is never changed or cut. The appended piece gets a heading line (time, "
            "source, dialog) and is embedded with every embedding model "
            "(text-search-doc, text-search-query, text-embeddings-v2-doc, "
            "text-embeddings-v2-query), each vector stored with its model version. "
            "The whole text is then put in the index (the knowledge vector stores "
            "file_search searches). Use it every turn to record your own message and "
            "how you see this turn."
        ),
        parameters=object_schema(
            {
                "text": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The text to append, in full",
                },
                "source": _optional(
                    {
                        "type": "string",
                        "description": "Who writes it: model (when not given) or a tool name",
                    }
                ),
            },
            ["text"],
        ),
        read_only=False,
        func=cache_append,
    ),
    "cache_read": _definition(
        title="Cache: read",
        description=(
            "Read the cache text: all of it, or length characters from offset "
            "(a negative offset counts from the end)."
        ),
        parameters=object_schema(
            {
                "offset": _optional(
                    {
                        "type": "integer",
                        "description": "First character; negative: from the end; 0 or not given: start",
                    }
                ),
                "length": _optional(
                    {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Characters to read; 0 or not given: to the end",
                    }
                ),
            },
            [],
        ),
        read_only=True,
        func=cache_read,
    ),
    "cache_search": _definition(
        title="Cache: search",
        description=(
            "Search the cache text. mode text: substring, regex: regular expression "
            "(both case-insensitive; every match with its position and the appended "
            "piece it is in); semantic: every appended piece ranked by cosine similarity "
            "in each embedding model; all (when not given): semantic ranking with text "
            "matches first. rank_by: mean of the models (when not given) or one model."
        ),
        parameters=object_schema(
            {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "description": "What to find",
                },
                "mode": _optional(
                    {
                        "type": "string",
                        "enum": list(SEARCH_MODES),
                        "description": "all when not given",
                    }
                ),
                "rank_by": _optional(
                    {
                        "type": "string",
                        "enum": [
                            "mean",
                            "text-search-doc",
                            "text-search-query",
                            "text-embeddings-v2-doc",
                            "text-embeddings-v2-query",
                        ],
                        "description": "Ranking score: mean (when not given) or one model",
                    }
                ),
                "limit": _optional(
                    {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Results to return; 0 or not given: all",
                    }
                ),
            },
            ["query"],
        ),
        read_only=True,
        func=cache_search,
    ),
}


__all__ = [
    "CACHE_FILE_NAME",
    "CACHE_TOOLS",
    "INDEX_FILE_NAME",
    "SEARCH_MODES",
    "cache_append",
    "cache_file_path",
    "cache_read",
    "cache_search",
]
