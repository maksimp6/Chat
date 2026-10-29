"""Append-only text cache with vectors from every embedding model (category ``cache``).

``CACHE_TOOLS`` holds three tools: ``cache_append`` adds a text, ``cache_read`` reads
entries back and ``cache_search`` finds them by text, regular expression or meaning.
The model and other tools write to one cache shared by every dialog; an entry keeps
the dialog it came from. Nothing in the cache is ever changed or removed.

Every appended text is embedded with each model of ``EMBEDDING_MODELS`` (v2 models at
their largest size), and each vector is stored with the model URI it came from, so
the entry records the version of every embedding model. A model that fails is listed
in ``embedding_errors``; the text is stored either way. A semantic search embeds the
question with the query models and compares it with each stored vector: a document
vector with its paired query model, a query vector with the same model.

Every entry is also put in the index: it is uploaded as a Markdown file
(``logos-cache-<key>.md``) and added to each knowledge vector store
(``knowledge_vector_store_ids``), the store the dialog's File Search searches. The
entry keeps the file id and each store's answer; a failed upload or store is listed in
``index_errors``, and the entry is stored either way.

The cache is one JSON Lines file next to the memory file (``logos.cache.jsonl``).
Appends hold an exclusive file lock, so processes sharing the file do not interleave.
There is no limit on the number or size of entries (0 means no application limit).
"""

from __future__ import annotations

import fcntl
import json
import re
import threading
import uuid
from collections.abc import Mapping
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

CACHE_FILE_NAME = "logos.cache.jsonl"
CACHE_FORMAT_VERSION = 2
SEARCH_MODES = ("all", "text", "regex", "semantic")
_TRANSPORTS = ["responses_api", "local_agent", "mcp"]
_lock = threading.Lock()


def cache_file_path() -> Path:
    """The cache file: ``logos.cache.jsonl`` beside the memory file."""
    return memory_file_path().with_name(CACHE_FILE_NAME)


# ---------------------------------------------------------------------------
# Caller context


def _call_of(cfg: Mapping[str, Any] | None) -> Any:
    context = cfg.get("_universal_context") if isinstance(cfg, Mapping) else None
    return context.get("call") if isinstance(context, Mapping) else None


def _caller(cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    """Transport, user and dialog of the call, as far as the call carries them."""
    call = _call_of(cfg)
    if call is None:
        return {"transport": None, "user_id": None, "conversation_id": None}
    metadata = getattr(call, "metadata", None)
    trace = metadata.get("execution_trace") if isinstance(metadata, Mapping) else None
    data = getattr(trace, "trace", None)
    context = data.get("context") if isinstance(data, Mapping) else None
    conversation = (
        context.get("conversation_id") if isinstance(context, Mapping) else None
    )
    return {
        "transport": str(getattr(call, "transport", "") or "") or None,
        "user_id": str(getattr(call, "user_id", "") or "") or None,
        "conversation_id": str(conversation or "") or None,
    }


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
        return {
            "error": exc.code,
            "message": str(exc),
            "tokens": exc.tokens_spent,
        }
    except ValueError as exc:
        return {"error": "invalid_embedding_request", "message": str(exc), "tokens": 0}
    return {
        "model_uri": result["model_uri"],
        "upstream": spec.get("upstream"),
        "dimensions": result["dimensions"],
        "vector": result["vectors"][0],
        "tokens": result["tokens"],
    }


def _trace_of(cfg: Mapping[str, Any] | None) -> Any:
    metadata = getattr(_call_of(cfg), "metadata", None)
    return metadata.get("execution_trace") if isinstance(metadata, Mapping) else None


# ---------------------------------------------------------------------------
# Index (knowledge vector stores)


def _index_document(entry: Mapping[str, Any]) -> str:
    """The entry as the Markdown file File Search reads: its fields, then the text."""
    fields = [
        ("key", entry.get("key")),
        ("created_at", entry.get("created_at")),
        ("source", entry.get("source")),
        ("tags", ", ".join(entry.get("tags") or [])),
        ("conversation_id", entry.get("conversation_id")),
        ("transport", entry.get("transport")),
        ("user_id", entry.get("user_id")),
    ]
    header = "\n".join(f"- {name}: {value}" for name, value in fields if value)
    return (
        f"# Logos cache entry {entry.get('key')}\n\n{header}\n\n{entry.get('text')}\n"
    )


def _index(entry: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Upload the entry and add it to every knowledge vector store: (index, errors)."""
    # Provider errors, credential errors (RuntimeError) and network errors (OSError).
    failures = (bound.YandexClientError, RuntimeError, OSError, ValueError)
    filename = f"logos-cache-{entry.get('key')}.md"
    index: dict[str, Any] = {"filename": filename, "file_id": None, "vector_stores": {}}
    errors: dict[str, Any] = {}
    try:
        client = bound.YandexResponsesClient(bound.Config)
        uploaded = client.upload_file(
            _index_document(entry).encode("utf-8"), filename, purpose="assistants"
        )
    except failures as exc:
        errors["upload"] = {"error": type(exc).__name__, "message": str(exc)}
        return index, errors
    file_id = uploaded.get("id") if isinstance(uploaded, Mapping) else None
    if not file_id:
        errors["upload"] = {"error": "no_file_id", "message": str(uploaded)}
        return index, errors
    index["file_id"] = file_id
    for store_id in knowledge_vector_store_ids():
        try:
            added = client.add_file_to_vs(store_id, file_id)
        except failures as exc:
            errors[store_id] = {"error": type(exc).__name__, "message": str(exc)}
            continue
        index["vector_stores"][store_id] = {
            "id": added.get("id") if isinstance(added, Mapping) else None,
            "status": added.get("status") if isinstance(added, Mapping) else None,
        }
    return index, errors


# ---------------------------------------------------------------------------
# Storage


def _read_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def _public(entry: Mapping[str, Any]) -> dict[str, Any]:
    """An entry without its vectors: each model keeps its URI, size and tokens."""
    result = {key: value for key, value in entry.items() if key != "embeddings"}
    result["embeddings"] = {
        name: {key: value for key, value in record.items() if key != "vector"}
        for name, record in (entry.get("embeddings") or {}).items()
    }
    return result


# ---------------------------------------------------------------------------
# Tools


def cache_append(arguments: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    """Append one text to the cache with a vector from every embedding model."""
    text = arguments.get("text") if isinstance(arguments, Mapping) else None
    if not isinstance(text, str) or not text.strip():
        return _failure("text must be a non-empty string", "validation")
    source = arguments.get("source")
    source = source.strip() if isinstance(source, str) and source.strip() else "model"
    tags = arguments.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        return _failure("tags must be a list of strings", "validation")

    trace = _trace_of(cfg)
    embeddings: dict[str, Any] = {}
    errors: dict[str, Any] = {}
    tokens = 0
    for name, spec in _embedding_models().items():
        record = _embed(text, name, spec, trace)
        tokens += record.get("tokens") or 0
        if "error" in record:
            errors[name] = record
        else:
            embeddings[name] = record

    created_at = datetime.now(UTC)
    entry: dict[str, Any] = {
        "format_version": CACHE_FORMAT_VERSION,
        # Names the entry before it has an id: the index file is uploaded first.
        "key": f"{created_at:%Y%m%dT%H%M%S%fZ}-{uuid.uuid4().hex[:8]}",
        "created_at": created_at.isoformat(),
        "source": source,
        "tags": list(dict.fromkeys(tags)),
        **_caller(cfg),
        "text": text,
        "chars": len(text),
        "embedding_tokens": tokens,
        "embeddings": embeddings,
        "embedding_errors": errors,
    }
    entry["index"], entry["index_errors"] = _index(entry)
    path = cache_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            handle.seek(0)
            entry = {"id": sum(1 for line in handle if line.strip()) + 1, **entry}
            handle.seek(0, 2)
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            handle.flush()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    return {"success": True, "path": str(path), "entry": _public(entry)}


def _matches(
    entry: Mapping[str, Any], conversation_id: Any, source: Any, tag: Any
) -> bool:
    if conversation_id and entry.get("conversation_id") != conversation_id:
        return False
    if source and entry.get("source") != source:
        return False
    return not tag or tag in (entry.get("tags") or [])


def cache_read(arguments: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    """Entries by id, or a page of entries filtered by dialog, source and tag."""
    arguments = arguments if isinstance(arguments, Mapping) else {}
    ids = arguments.get("ids")
    if ids is not None and (
        not isinstance(ids, list) or not all(type(item) is int for item in ids)
    ):
        return _failure("ids must be a list of integers", "validation")
    offset = arguments.get("offset") or 0
    limit = arguments.get("limit") or 0
    if type(offset) is not int or offset < 0 or type(limit) is not int or limit < 0:
        return _failure("offset and limit must be integers of 0 or more", "validation")
    newest_first = arguments.get("order") != "asc"

    path = cache_file_path()
    entries = _read_entries(path)
    total = len(entries)
    if ids:
        wanted = set(ids)
        selected = [entry for entry in entries if entry.get("id") in wanted]
    else:
        selected = [
            entry
            for entry in entries
            if _matches(
                entry,
                arguments.get("conversation_id"),
                arguments.get("source"),
                arguments.get("tag"),
            )
        ]
    if newest_first:
        selected.reverse()
    matched = len(selected)
    selected = selected[offset:]
    if limit:
        selected = selected[:limit]
    return {
        "success": True,
        "path": str(path),
        "total": total,
        "matched": matched,
        "entries": [_public(entry) for entry in selected],
    }


def cache_search(arguments: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    """Find entries by substring, regular expression or meaning (every embedding model)."""
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

    path = cache_file_path()
    entries = [
        entry
        for entry in _read_entries(path)
        if _matches(
            entry,
            arguments.get("conversation_id"),
            arguments.get("source"),
            arguments.get("tag"),
        )
    ]
    needle = query.casefold()
    results = []
    for entry in entries:
        text = str(entry.get("text") or "")
        text_match = (
            bool(pattern.search(text))
            if pattern is not None
            else needle in text.casefold()
        )
        results.append({"entry": entry, "text_match": text_match})

    if mode in ("text", "regex"):
        hits = [item for item in reversed(results) if item["text_match"]]
        if limit:
            hits = hits[:limit]
        return {
            "success": True,
            "path": str(path),
            "mode": mode,
            "searched": len(entries),
            "results": [
                {"text_match": True, **_public(item["entry"])} for item in hits
            ],
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

    for item in results:
        scores = {}
        for name, record in (item["entry"].get("embeddings") or {}).items():
            vector = query_vectors.get(_query_model_for(name))
            if vector is not None and isinstance(record.get("vector"), list):
                scores[name] = cosine_similarity(vector, record["vector"])
        item["scores"] = scores
        if rank_by == "mean":
            item["score"] = sum(scores.values()) / len(scores) if scores else None
        else:
            item["score"] = scores.get(rank_by)

    ranked = sorted(
        results,
        key=lambda item: (
            item["score"] is not None,
            mode == "all" and item["text_match"],
            item["score"] if item["score"] is not None else 0.0,
        ),
        reverse=True,
    )
    if mode == "all":
        ranked = [
            item for item in ranked if item["text_match"] or item["score"] is not None
        ]
    if limit:
        ranked = ranked[:limit]
    return {
        "success": True,
        "path": str(path),
        "mode": mode,
        "rank_by": rank_by,
        "searched": len(entries),
        "query_embedding_tokens": tokens,
        "results": [
            {
                "score": item["score"],
                "scores": item["scores"],
                "text_match": item["text_match"],
                **_public(item["entry"]),
            }
            for item in ranked
        ],
    }


# ---------------------------------------------------------------------------
# Definitions


def _optional(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


_FILTERS = {
    "conversation_id": _optional(
        {"type": "string", "description": "Only entries written in this dialog"}
    ),
    "source": _optional(
        {
            "type": "string",
            "description": "Only entries of this source (model, a tool name)",
        }
    ),
    "tag": _optional(
        {"type": "string", "description": "Only entries carrying this tag"}
    ),
}


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
            "Append a text to the Logos cache. The cache only grows: an entry is never "
            "changed or removed. The text is embedded with every embedding model "
            "(text-search-doc, text-search-query, text-embeddings-v2-doc, "
            "text-embeddings-v2-query) and each vector is stored with its model version; the "
            "entry is also put in the index (the knowledge vector stores file_search searches). "
            "Use it every turn to record your own message and how you see this turn."
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
                "tags": _optional(
                    {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Labels to find the entry by, e.g. answer, self-view",
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
            "Read cache entries: the ones named by ids, or the entries filtered by dialog, "
            "source and tag, newest first unless order is asc. Vectors are not returned; "
            "each entry lists its embedding models with their versions and sizes."
        ),
        parameters=object_schema(
            {
                "ids": _optional(
                    {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Entry ids to read",
                    }
                ),
                **_FILTERS,
                "order": _optional(
                    {
                        "type": "string",
                        "enum": ["desc", "asc"],
                        "description": "desc: newest first",
                    }
                ),
                "offset": _optional(
                    {"type": "integer", "minimum": 0, "description": "Entries to skip"}
                ),
                "limit": _optional(
                    {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Entries to return; 0 or not given: all",
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
            "Search the cache. mode text: substring, regex: regular expression (both "
            "case-insensitive, newest first); semantic: every entry ranked by cosine "
            "similarity in each embedding model; all (when not given): semantic ranking "
            "with the text match marked. Each result has a score per embedding model and "
            "the ranking score (rank_by: mean of the models, or one model)."
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
                **_FILTERS,
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
    "CACHE_FORMAT_VERSION",
    "CACHE_TOOLS",
    "SEARCH_MODES",
    "cache_append",
    "cache_file_path",
    "cache_read",
    "cache_search",
]
