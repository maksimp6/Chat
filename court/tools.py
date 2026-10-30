"""Court tools (category ``court``): the judges over a dialog and the hall of hearings.

Reading is free: the rules, a dry protocol of a dialog, the list of hearings and one
hearing. Everything that changes the hall needs the owner's approval: opening a sitting,
judging again, a decision on a case (recorded in the owner's name, because the approval
is theirs — a decision by the executor would be S2.5, ТЯЖКОЕ), filing a case to GitHub,
closing and reopening a sitting.
"""

from __future__ import annotations

from typing import Any

from court._vendor.schema import object_schema
from court.hearings import (
    DECISIONS,
    close_hearing,
    decide_case,
    file_hearing_case,
    get_hearing,
    list_hearings,
    open_hearing,
    rejudge_hearing,
    reopen_hearing,
    speak,
)
from court.host import check_access, get_current_owner_id, get_messages
from court.judging import judge_dialog, rules_table

__all__ = ["COURT_TOOLS"]


def _tool(
    fn,
    *,
    read_only: bool,
    requires_approval: bool,
    schema: dict[str, Any],
    title: str,
    description: str,
) -> dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "parameters": schema,
        "capabilities": ["court"],
        "risk_level": "low" if read_only else "high",
        "read_only": read_only,
        "requires_approval": requires_approval,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "func": fn,
    }


_HEARING = {"hearing_id": {"type": "string", "minLength": 1, "maxLength": 128}}
_CASE = {"case": {"type": "integer", "minimum": 1}}
_CRISIS = {
    "crisis": {
        "type": "boolean",
        "description": "Кризисный режим: вопрос о состоянии — забота (S4.5), а не вторжение (S4.4)",
    }
}


def _rules(args, cfg=None):
    return {"rules": rules_table(), "decisions": list(DECISIONS)}


def _judge(args, cfg=None):
    crisis = bool(args.get("crisis"))
    messages = args.get("messages")
    if messages is None:
        conversation_id = str(args.get("conversation_id") or "")
        if not conversation_id or not check_access(
            conversation_id, get_current_owner_id(required=False)
        ):
            return {"success": False, "error": "conversation_not_found"}
        messages = get_messages(conversation_id)
    elif not isinstance(messages, list):
        return {"success": False, "error": "messages must be a list"}
    return {"success": True, "protocol": judge_dialog(messages, crisis=crisis)}


def _list(args, cfg=None):
    return {"success": True, "hearings": list_hearings()}


def _get(args, cfg=None):
    return {"success": True, "hearing": get_hearing(str(args.get("hearing_id") or ""))}


def _open(args, cfg=None):
    hearing = open_hearing(
        str(args.get("conversation_id") or ""), crisis=bool(args.get("crisis"))
    )
    return {"success": True, "hearing": hearing}


def _rejudge(args, cfg=None):
    crisis = args.get("crisis")
    hearing = rejudge_hearing(
        str(args.get("hearing_id") or ""),
        crisis=None if crisis is None else bool(crisis),
    )
    return {"success": True, "hearing": hearing}


def _decide(args, cfg=None):
    hearing = decide_case(
        str(args.get("hearing_id") or ""),
        int(args.get("case") or 0),
        str(args.get("decision") or ""),
        str(args.get("reason") or ""),
    )
    return {"success": True, "hearing": hearing}


def _file(args, cfg=None):
    hearing = file_hearing_case(
        str(args.get("hearing_id") or ""), int(args.get("case") or 0)
    )
    return {"success": True, "hearing": hearing}


def _speak(args, cfg=None):
    crisis = args.get("crisis")
    return {
        "success": True,
        **speak(
            str(args.get("conversation_id") or ""),
            crisis=None if crisis is None else bool(crisis),
        ),
    }


def _close(args, cfg=None):
    hearing = close_hearing(
        str(args.get("hearing_id") or ""), str(args.get("reason") or "")
    )
    return {"success": True, "hearing": hearing}


def _reopen(args, cfg=None):
    hearing = reopen_hearing(
        str(args.get("hearing_id") or ""), str(args.get("reason") or "")
    )
    return {"success": True, "hearing": hearing}


# The whole hall is one tool: ``action`` names what to do. Each action keeps its own
# function, the fields it needs and whether it changes the hall.
_ACTIONS: dict[str, dict[str, Any]] = {
    "rules": {"fn": _rules, "required": [], "write": False,
              "about": "правила судов и состояния решений"},
    "judge_dialog": {"fn": _judge, "required": [], "write": False,
                     "about": "суд над диалогом без записи заседания (conversation_id или messages)"},
    "hearings_list": {"fn": _list, "required": [], "write": False,
                      "about": "заседания зала"},
    "hearing_get": {"fn": _get, "required": ["hearing_id"], "write": False,
                    "about": "одно заседание целиком"},
    "hearing_open": {"fn": _open, "required": ["conversation_id"], "write": True,
                     "about": "открыть заседание над диалогом"},
    "hearing_rejudge": {"fn": _rejudge, "required": ["hearing_id"], "write": True,
                        "about": "пересмотр заседания: суды проходят заново"},
    "hearing_decide": {"fn": _decide, "required": ["hearing_id", "case", "decision", "reason"], "write": True,
                       "about": "решение владельца по делу заседания"},
    "case_file": {"fn": _file, "required": ["hearing_id", "case"], "write": True,
                  "about": "подать дело заседания в GitHub"},
    "speak": {"fn": _speak, "required": ["conversation_id"], "write": True,
              "about": "слово суда в групповом чате диалога"},
    "hearing_close": {"fn": _close, "required": ["hearing_id", "reason"], "write": True,
                      "about": "закрыть заседание с причиной"},
    "hearing_reopen": {"fn": _reopen, "required": ["hearing_id", "reason"], "write": True,
                       "about": "возобновить закрытое заседание с причиной"},
}


def court(args, cfg=None):
    """One function for the hall: ``action`` picks the action, the other fields are its input."""
    action = args.get("action")
    entry = _ACTIONS.get(action) if isinstance(action, str) else None
    if entry is None:
        return {"success": False, "error": "action must be one of: " + ", ".join(_ACTIONS)}
    missing = [name for name in entry["required"] if args.get(name) in (None, "")]
    if missing:
        return {"success": False, "error": f"{action}: missing {', '.join(missing)}"}
    return entry["fn"](args, cfg)


_FIELDS = {
    "action": {
        "type": "string",
        "enum": list(_ACTIONS),
        "description": "Что сделать: " + "; ".join(f"{name} — {entry['about']}" for name, entry in _ACTIONS.items()),
    },
    "conversation_id": {"type": "string", "minLength": 1, "maxLength": 128},
    "messages": {
        "type": "array",
        "description": "Вместо conversation_id (judge_dialog): сообщения с role, text, created_at и, при наличии, kind",
        "items": {"type": "object"},
    },
    "decision": {"type": "string", "enum": list(DECISIONS)},
    "reason": {"type": "string", "minLength": 1},
    **_HEARING,
    **_CASE,
    **_CRISIS,
}

_WRITE_ACTIONS = [name for name, entry in _ACTIONS.items() if entry["write"]]

COURT_TOOLS = {
    "court": {
        **_tool(
            court,
            read_only=False,
            requires_approval=True,
            schema=object_schema(_FIELDS, ["action"]),
            title="Court",
            description="Зал суда Логоса одной функцией: поле action выбирает действие. "
            + "; ".join(f"{name} — {entry['about']}" for name, entry in _ACTIONS.items())
            + ". Действия, которые меняют зал, требуют подтверждения владельца.",
        ),
        "metadata": {"approval_actions": _WRITE_ACTIONS},
    },
}
