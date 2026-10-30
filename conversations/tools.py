"""Dialog tools (category ``dialog``): Logos reads the owner's dialogs.

Reading only: the list of dialogs, one dialog and the messages of one dialog. The calls
use the owner and the checks of the MCP conversation tools, so the model sees the same
dialogs the MCP clients see.
"""

from __future__ import annotations

from typing import Any

from conversations.host import get_current_owner_id, get_messages
from conversations.ownership import (
    check_access,
    get_owned_conversation,
    list_owned_conversations,
)

__all__ = ["CONVERSATION_TOOLS"]


def _list(args, cfg=None):
    return {
        "success": True,
        "conversations": list_owned_conversations(get_current_owner_id()),
    }


def _get(args, cfg=None):
    conversation = get_owned_conversation(
        str(args.get("conversation_id") or ""), get_current_owner_id()
    )
    if conversation is None:
        return {"success": False, "error": "conversation_not_found"}
    return {"success": True, "conversation": conversation}


def _messages(args, cfg=None):
    conversation_id = str(args.get("conversation_id") or "")
    if not check_access(conversation_id, get_current_owner_id()):
        return {"success": False, "error": "conversation_not_found"}
    return {
        "success": True,
        "conversation_id": conversation_id,
        "messages": get_messages(conversation_id),
    }


# The dialogs are one tool: ``action`` names what to do, the other fields are its input.
_ACTIONS: dict[str, dict[str, Any]] = {
    "list": {"fn": _list, "required": [], "about": "список диалогов владельца"},
    "get": {
        "fn": _get,
        "required": ["conversation_id"],
        "about": "один диалог целиком (conversation_id)",
    },
    "messages": {
        "fn": _messages,
        "required": ["conversation_id"],
        "about": "сообщения диалога (conversation_id)",
    },
}


def dialog(args, cfg=None):
    """One function for the dialogs: ``action`` picks the action, the other fields are its input."""
    action = args.get("action")
    entry = _ACTIONS.get(action) if isinstance(action, str) else None
    if entry is None:
        return {
            "success": False,
            "error": "action must be one of: " + ", ".join(_ACTIONS),
        }
    missing = [name for name in entry["required"] if args.get(name) in (None, "")]
    if missing:
        return {"success": False, "error": f"{action}: missing {', '.join(missing)}"}
    return entry["fn"](args, cfg)


_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(_ACTIONS),
            "description": "Что сделать: "
            + "; ".join(f"{name} — {entry['about']}" for name, entry in _ACTIONS.items()),
        },
        "conversation_id": {
            "type": "string",
            "minLength": 1,
            "description": "Диалог: нужен для get и messages",
        },
    },
    "required": ["action"],
    "additionalProperties": False,
}

CONVERSATION_TOOLS = {
    "dialog": {
        "title": "Dialog",
        "description": "Диалоги владельца Логоса одной функцией: поле action выбирает действие. "
        + "; ".join(f"{name} — {entry['about']}" for name, entry in _ACTIONS.items())
        + ". Только чтение: диалоги не меняются.",
        "parameters": _SCHEMA,
        "capabilities": ["conversation"],
        "risk_level": "low",
        "read_only": True,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": dialog,
    },
}
