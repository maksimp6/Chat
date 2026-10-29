"""The Logos cache: one long text that only grows (category ``cache``).

``CACHE_TOOLS`` holds three tools: ``cache_append`` adds text to the end,
``cache_read`` reads the whole text (it is only read in full) and ``cache_search``
hands a question to a helper model, which reads the whole text and answers with what
it found there. The model and other tools write to one text shared by every dialog.
Nothing in it is ever changed or removed.

Each append is one piece of the text: a heading line (time, source, dialog) and the
appended text. ``cache_append`` writes the piece and returns at once; one background
thread then embeds it and refreshes the index, so a turn never waits for either. The
result of the last finished background job (its ``embedding_errors`` and
``index_errors``) comes back with the next append. Pieces still queued when the
process stops keep their text but get no vectors. Every piece is embedded with each model of ``EMBEDDING_MODELS`` (v2
models at their largest size); the vectors are kept beside the text with the model
URI each came from, so the version of every embedding model is recorded. A model
that fails is listed in the piece's ``embedding_errors``; the text is appended either
way.

The search is a helper model (``SEARCH_MODEL``, DeepSeek V4.1 Flash): it gets
the whole cache text and the question in one provider request without tools, and its
answer is returned with the request's status, errors and usage. The request is
recorded on the caller's execution trace.

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
import contextvars
import fcntl
import json
import logging
import queue
import threading
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tool_providers.host import (
    bound,
    embed_texts,
    knowledge_vector_store_ids,
    memory_file_path,
)
from tool_providers.schema import object_schema

CACHE_FILE_NAME = "logos.cache.md"
VECTORS_FILE_NAME = "logos.cache.vectors.jsonl"
INDEX_STATE_FILE_NAME = "logos.cache.index.json"
INDEX_FILE_NAME = "logos-cache.md"
# The search model: the text model with the largest context window (1M tokens).
SEARCH_MODEL = "deepseek-v4.1-flash"
SEARCH_ROLE = (
    "Ты — модель-помощник Логоса для поиска в кэше. Кэш — один длинный текст, который "
    "только растёт: в него дописывают модель и инструменты всех диалогов; каждый кусок "
    "начинается строкой «## время · источник · диалог». Тебе дан весь текст кэша и "
    "запрос основной модели. Найди в кэше всё, что относится к запросу, и верни: "
    "найденные места дословно, с заголовками их кусков (время, источник, диалог), и "
    "краткий вывод по запросу. Опирайся только на текст кэша; если в нём ничего "
    "подходящего нет, так и скажи. Не придумывай записи, даты и источники."
)
_TRANSPORTS = ["responses_api", "local_agent", "mcp"]
logger = logging.getLogger("logos.tool_providers.cache")
_text_lock = threading.Lock()
_vectors_lock = threading.Lock()
_index_lock = threading.Lock()
# Pieces waiting for their embeddings and the index refresh, and the last one done.
_jobs: queue.Queue[tuple[dict[str, Any], contextvars.Context]] = queue.Queue()
_jobs_lock = threading.Lock()
_last_job: dict[str, Any] = {}
_worker_thread: threading.Thread | None = None


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


def _process(piece: dict[str, Any]) -> None:
    """Embed one appended piece with every model and record its vectors."""
    embeddings: dict[str, Any] = {}
    embedding_errors: dict[str, Any] = {}
    tokens = 0
    for name, spec in _embedding_models().items():
        record = _embed(piece["text"], name, spec, None)
        tokens += record.get("tokens") or 0
        if "error" in record:
            embedding_errors[name] = record
        else:
            embeddings[name] = record
    record = {key: value for key, value in piece.items() if key not in ("text", "path")}
    record.update(
        embedding_tokens=tokens,
        embeddings=embeddings,
        embedding_errors=embedding_errors,
    )
    path = _vectors_path()
    with _locked(_vectors_lock, path), path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    with _jobs_lock:
        _last_job.clear()
        _last_job.update(
            start=piece["start"],
            created_at=piece["created_at"],
            finished_at=datetime.now(UTC).isoformat(),
            embedding_errors=embedding_errors,
        )


def _worker() -> None:
    """Embed queued pieces in order; refresh the index once the queue is empty.

    Each piece runs in a copy of its caller's context, so the runtime data root and
    the credential are the caller's. Index refreshes wait until the queue is empty:
    one upload per cache file carries every piece appended meanwhile.
    """
    pending: dict[str, contextvars.Context] = {}
    while True:
        piece, context = _jobs.get()
        try:
            context.run(_process, piece)
        except Exception:
            logger.exception("[CACHE] piece at %s was not embedded", piece.get("start"))
            with _jobs_lock:
                _last_job.update(start=piece.get("start"), crashed="embedding")
        pending[piece["path"]] = context
        if _jobs.empty():
            for context in pending.values():
                try:
                    index, index_errors = context.run(_refresh_index)
                except Exception:
                    logger.exception("[CACHE] the index was not refreshed")
                    index, index_errors = {}, {"refresh": {"error": "crashed"}}
                with _jobs_lock:
                    _last_job.update(index=index, index_errors=index_errors)
            pending.clear()
        _jobs.task_done()


def _enqueue(piece: dict[str, Any]) -> None:
    global _worker_thread
    with _jobs_lock:
        if _worker_thread is None or not _worker_thread.is_alive():
            _worker_thread = threading.Thread(
                target=_worker, name="logos-cache", daemon=True
            )
            _worker_thread.start()
    _jobs.put((piece, contextvars.copy_context()))


def cache_append(arguments: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    """Append text to the end of the cache now; embeddings and the index follow in the background."""
    text = arguments.get("text") if isinstance(arguments, Mapping) else None
    if not isinstance(text, str) or not text.strip():
        return _failure("text must be a non-empty string", "validation")
    source = arguments.get("source")
    source = source.strip() if isinstance(source, str) and source.strip() else "model"

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
    }
    with _jobs_lock:
        previous = dict(_last_job)
    _enqueue({**piece, "path": str(path), "text": text})
    return {
        "success": True,
        "path": str(path),
        "total_chars": start + len(piece_text),
        "piece": piece,
        "background": {"queued": _jobs.qsize(), "previous": previous or None},
    }


def cache_read(arguments: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    """The whole cache text: it is only ever read in full."""
    text = _read_text()
    return {
        "success": True,
        "path": str(cache_file_path()),
        "total_chars": len(text),
        "text": text,
    }


def cache_search(arguments: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    """A helper model reads the whole cache and answers what it found for the query."""
    arguments = arguments if isinstance(arguments, Mapping) else {}
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return _failure("query must be a non-empty string", "validation")

    text = _read_text()
    client = bound.YandexResponsesClient(bound.Config)
    response = client.ask(
        f"Запрос основной модели:\n{query.strip()}\n\n"
        f"Весь текст кэша ({len(text)} символов):\n\n{text}",
        SEARCH_MODEL,
        params={"instructions": SEARCH_ROLE, "reasoning_effort": "high"},
        execution_trace=_trace_of(cfg),
    )
    reasoning, answer = client.extract_reasoning_and_text(response)
    return {
        "success": True,
        "path": str(cache_file_path()),
        "total_chars": len(text),
        "model": SEARCH_MODEL,
        "response_id": response.get("id"),
        "status": response.get("status"),
        "error": response.get("error"),
        "incomplete_details": response.get("incomplete_details"),
        "answer": answer,
        "reasoning": reasoning,
        "usage": client.extract_usage(response),
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
            "source, dialog); the call returns at once. In the background the piece is "
            "embedded with every embedding model (text-search-doc, text-search-query, "
            "text-embeddings-v2-doc, text-embeddings-v2-query), each vector stored with "
            "its model version, and the whole text is put in the index (the knowledge "
            "vector stores file_search searches); background.previous reports the last "
            "finished background job and its errors. Use it every turn to record your "
            "own message and how you see this turn."
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
                        "description": "Who writes it: user (the question), model (when not given) or a tool name",
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
        description="Read the whole cache text, from the first piece to the last.",
        parameters=object_schema({}, []),
        read_only=True,
        func=cache_read,
    ),
    "cache_search": _definition(
        title="Cache: search",
        description=(
            "Search the cache with a helper model: it reads the whole cache text and "
            "answers the query with the places it found, quoted with their headings "
            "(time, source, dialog), and a short conclusion."
        ),
        parameters=object_schema(
            {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "description": "What to find in the cache, in full",
                },
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
    "SEARCH_MODEL",
    "SEARCH_ROLE",
    "cache_append",
    "cache_file_path",
    "cache_read",
    "cache_search",
]
