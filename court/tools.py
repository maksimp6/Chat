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


COURT_TOOLS = {
    "court.rules": _tool(
        _rules,
        read_only=True,
        requires_approval=False,
        schema=object_schema({}, []),
        title="Court Rules",
        description="Правила судов Логоса (S2, S4, S5, S3) с вердиктами и состояния решений по делу.",
    ),
    "court.judge_dialog": _tool(
        _judge,
        read_only=True,
        requires_approval=False,
        schema=object_schema(
            {
                "conversation_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "messages": {
                    "type": "array",
                    "description": "Вместо conversation_id: сообщения с role, text, created_at и, при наличии, kind",
                    "items": {"type": "object"},
                },
                **_CRISIS,
            },
            [],
        ),
        title="Court Judge Dialog",
        description="Суд над диалогом без записи заседания: протокол по сообщениям — номер, вердикт, цитата, правило; вспышки и черновики дел.",
    ),
    "court.hearings.list": _tool(
        _list,
        read_only=True,
        requires_approval=False,
        schema=object_schema({}, []),
        title="Court Hearings List",
        description="Заседания зала: диалог, вердикт, число дел и их решения.",
    ),
    "court.hearing.get": _tool(
        _get,
        read_only=True,
        requires_approval=False,
        schema=object_schema(_HEARING, ["hearing_id"]),
        title="Court Hearing Get",
        description="Одно заседание целиком: протокол, вспышки, дела, решения и их история.",
    ),
    "court.hearing.open": _tool(
        _open,
        read_only=False,
        requires_approval=True,
        schema=object_schema(
            {
                "conversation_id": {"type": "string", "minLength": 1, "maxLength": 128},
                **_CRISIS,
            },
            ["conversation_id"],
        ),
        title="Court Hearing Open",
        description="Открыть заседание над диалогом: суды проходят по всем сообщениям, протокол и черновики дел сохраняются в зале.",
    ),
    "court.hearing.rejudge": _tool(
        _rejudge,
        read_only=False,
        requires_approval=True,
        schema=object_schema({**_HEARING, **_CRISIS}, ["hearing_id"]),
        title="Court Hearing Rejudge",
        description="Пересмотр: суды проходят по диалогу заново, решения по сохранившимся делам остаются с записью в истории.",
    ),
    "court.hearing.decide": _tool(
        _decide,
        read_only=False,
        requires_approval=True,
        schema=object_schema(
            {
                **_HEARING,
                **_CASE,
                "decision": {"type": "string", "enum": list(DECISIONS)},
                "reason": {"type": "string", "minLength": 1},
            },
            ["hearing_id", "case", "decision", "reason"],
        ),
        title="Court Case Decide",
        description="Решение владельца по делу заседания (подтверждается владельцем); прежнее решение остаётся в истории.",
    ),
    "court.case.file": _tool(
        _file,
        read_only=False,
        requires_approval=True,
        schema=object_schema({**_HEARING, **_CASE}, ["hearing_id", "case"]),
        title="Court Case File",
        description="Подать дело заседания в GitHub: issue «Суд Логоса» с меткой from-dialog, цитаты — в зашифрованной улике.",
    ),
    "court.hearing.close": _tool(
        _close,
        read_only=False,
        requires_approval=True,
        schema=object_schema(
            {**_HEARING, "reason": {"type": "string", "minLength": 1}},
            ["hearing_id", "reason"],
        ),
        title="Court Hearing Close",
        description="Закрыть заседание с причиной.",
    ),
    "court.hearing.reopen": _tool(
        _reopen,
        read_only=False,
        requires_approval=True,
        schema=object_schema(
            {**_HEARING, "reason": {"type": "string", "minLength": 1}},
            ["hearing_id", "reason"],
        ),
        title="Court Hearing Reopen",
        description="Возобновить закрытое заседание с причиной.",
    ),
}
