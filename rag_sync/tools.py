"""The ``rag`` tool: one function, ``action`` picks what to do with the knowledge store's repository data."""

from __future__ import annotations

from typing import Any

from rag_sync.sync import reset, start, status

__all__ = ["RAG_TOOLS"]

_SOURCES = {"git": ("git",), "github": ("github",), "all": ("git", "github")}


def _sync(args, cfg=None):
    sources = _SOURCES.get(str(args.get("sources") or "all"))
    if sources is None:
        return {
            "success": False,
            "error": "sources must be one of: " + ", ".join(_SOURCES),
        }
    return {"success": True, **start(sources=sources)}


def _status(args, cfg=None):
    return {"success": True, **status()}


def _reset(args, cfg=None):
    return {"success": True, **reset()}


_ACTIONS: dict[str, dict[str, Any]] = {
    "sync": {
        "fn": _sync,
        "write": True,
        "about": "дописать в базу знаний новые коммиты git и новое с GitHub (задачи, PR, комментарии, "
        "прогоны) новыми файлами; старые файлы не трогаются; идёт в фоне, ход — в status",
    },
    "status": {
        "fn": _status,
        "write": False,
        "about": "курсоры, число файлов и состояние текущей или последней синхронизации",
    },
    "reset": {
        "fn": _reset,
        "write": True,
        "about": "удалить из Files и хранилища все пакеты репозитория и забыть курсоры; "
        "следующий sync загрузит всю историю заново",
    },
}


def rag(args, cfg=None):
    """One function for the repository knowledge: ``action`` picks the action."""
    action = args.get("action")
    entry = _ACTIONS.get(action) if isinstance(action, str) else None
    if entry is None:
        return {
            "success": False,
            "error": "action must be one of: " + ", ".join(_ACTIONS),
        }
    return entry["fn"](args, cfg)


RAG_TOOLS = {
    "rag": {
        "title": "Repository knowledge",
        "description": "База знаний репозитория одной функцией: поле action выбирает действие. "
        + "; ".join(f"{name} — {entry['about']}" for name, entry in _ACTIONS.items()),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "sources": {
                    "type": "string",
                    "enum": list(_SOURCES),
                    "description": "Для sync: git, github или all (по умолчанию all)",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        "capabilities": ["rag", "knowledge"],
        "risk_level": "high",
        "read_only": False,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent"],
        "executor": {"type": "local"},
        "func": rag,
        "metadata": {
            "approval_actions": [
                name for name, entry in _ACTIONS.items() if entry["write"]
            ]
        },
    }
}
