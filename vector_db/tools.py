"""The ``vectors`` tool: one function, ``action`` picks what to do with the app's vector database."""

from __future__ import annotations

from typing import Any

from vector_db import sense, store

__all__ = ["REMEMBER_TOOLS", "VECTOR_TOOLS"]

# The answer when every phrase chosen for this thought was already given in this turn.
REMEMBER_NOTHING_NEW = "Новых фраз нет: всё, что память дала на эту мысль, уже дано выше в этом ходе."


def _metadata_of(cfg) -> dict:
    """The metadata of the tool call, if the executor passed the call."""
    context = cfg.get("_universal_context") if isinstance(cfg, dict) else None
    call = context.get("call") if isinstance(context, dict) else None
    metadata = getattr(call, "metadata", None)
    return metadata if isinstance(metadata, dict) else {}


def _trace_of(cfg) -> Any:
    """The execution trace of the tool call (the turn's trace), if the executor passed one."""
    return _metadata_of(cfg).get("execution_trace")


def _shown_in_turn(trace: Any) -> set:
    """The phrases remember gave earlier in this turn: the sense_extracted events of the turn's trace."""
    record = getattr(trace, "trace", None)
    events = record.get("events") if isinstance(record, dict) else None
    return {
        str(given.get("phrase")).casefold()
        for event in events or ()
        if isinstance(event, dict) and event.get("type") == "sense_extracted"
        for given in (event.get("payload") or {}).get("phrases") or ()
        if isinstance(given, dict)
    }


def remember(args, cfg=None):
    """Memory by meaning: the vector database searched with what the model was thinking.

    ``context`` is the sense of the best chunks as one short text (vector_db.sense), without a
    phrase already given in this turn: a new thought can bring up other phrases of the same
    chunks, and the same thought gets an empty ``context`` and a ``note``. Ids, chunks and scores
    go into the trace (remember_results)."""
    thought = str(_metadata_of(cfg).get("thought") or "").strip()
    if not thought:
        return {"success": False, "error": "нет текста размышления: подумай о том, что нужно вспомнить, и вызови снова"}
    trace = _trace_of(cfg)
    found = store.search(thought, None, None, trace=trace)
    picked = sense.best_documents(thought, found["results"])
    extracted = sense.extract(thought, picked, trace=trace, shown=_shown_in_turn(trace))
    if trace is not None and hasattr(trace, "add_event"):
        trace.add_event("remember_results", {
            "searched": found["searched"],
            "given": [
                {key: hit.get(key) for key in ("collection", "id", "chunk", "score")} for hit in picked
            ],
        })
    result = {"success": True, "context": extracted["text"], "searched": found["searched"]}
    if picked and not extracted["text"]:
        result["note"] = REMEMBER_NOTHING_NEW
    return result


def _add(args, cfg=None):
    return {"success": True, **store.add(
        args.get("collection"), args.get("text"), args.get("id"), args.get("metadata"), trace=_trace_of(cfg)
    )}


def _search(args, cfg=None):
    k = args.get("k")
    return {"success": True, **store.search(
        args.get("query"), args.get("collection"), None if k is None else int(k), trace=_trace_of(cfg)
    )}


def _delete(args, cfg=None):
    return {"success": True, **store.delete(args.get("collection"), args.get("id"))}


def _drop(args, cfg=None):
    return {"success": True, **store.drop(args.get("collection"))}


def _collections(args, cfg=None):
    return {"success": True, **store.collections()}


_ACTIONS: dict[str, dict[str, Any]] = {
    "add": {"fn": _add, "required": ["collection", "text"], "write": True,
            "about": "встроить text в коллекцию collection (id — свой идентификатор документа, тот же id "
                     "заменяет документ; metadata — объект); текст режется на части, каждая — эмбеддинг"},
    "search": {"fn": _search, "required": ["query"], "write": False,
               "about": "поиск по смыслу: части, ближайшие к query (collection — одна коллекция, "
                        "иначе все; k — сколько вернуть, иначе все по убыванию сходства)"},
    "delete": {"fn": _delete, "required": ["collection", "id"], "write": True,
               "about": "удалить документ id из коллекции"},
    "drop": {"fn": _drop, "required": ["collection"], "write": True, "about": "удалить коллекцию целиком"},
    "collections": {"fn": _collections, "required": [], "write": False,
                    "about": "коллекции с числом документов и частей"},
}


def vectors(args, cfg=None):
    """One function for the vector database: ``action`` picks the action."""
    action = args.get("action")
    entry = _ACTIONS.get(action) if isinstance(action, str) else None
    if entry is None:
        return {"success": False, "error": "action must be one of: " + ", ".join(_ACTIONS)}
    missing = [name for name in entry["required"] if args.get(name) in (None, "")]
    if missing:
        return {"success": False, "error": f"{action}: missing {', '.join(missing)}"}
    try:
        return entry["fn"](args, cfg)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}


VECTOR_TOOLS = {
    "vectors": {
        "title": "Vector database",
        "description": "Векторная база приложения (эмбеддинги Ollama на ПК, поиск по смыслу на сервере) одной "
        "функцией: поле action выбирает действие. "
        + "; ".join(f"{name} — {entry['about']}" for name, entry in _ACTIONS.items()),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "collection": {"type": "string", "minLength": 1, "maxLength": 128},
                "id": {"type": "string", "minLength": 1, "maxLength": 256},
                "text": {"type": "string", "minLength": 1},
                "metadata": {"type": "object"},
                "query": {"type": "string", "minLength": 1},
                "k": {"type": "integer", "minimum": 1},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        "capabilities": ["vectors", "embeddings", "knowledge"],
        "risk_level": "low",
        "read_only": False,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent"],
        "executor": {"type": "local"},
        "func": vectors,
        "metadata": {"approval_actions": [name for name, entry in _ACTIONS.items() if entry["write"]]},
    }
}


REMEMBER_TOOLS = {
    "remember": {
        "title": "Remember",
        "description": "Вспомнить. Без параметров: сначала подумай, что именно нужно вспомнить, "
        "затем вызови. Память ищется по смыслу твоего размышления; context — короткий текст "
        "из самых близких по смыслу фраз памяти, не целые предложения.",
        # Open: a small model adds arguments of its own ({"what": ...}, seen 05.11.2026 on
        # qwen3:1.7b), and a closed schema refused the call. They are ignored: memory is
        # searched by the thinking text.
        "parameters": {"type": "object", "additionalProperties": True},
        "capabilities": ["remember", "memory", "vectors"],
        "risk_level": "low",
        "read_only": True,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent"],
        "executor": {"type": "local"},
        "func": remember,
    }
}
