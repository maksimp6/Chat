"""Зал заседаний: заседания над диалогами, протоколы судов, черновики дел и решения владельца.

A hearing (``court/hearing/<id>`` in the Kristall store) is one sitting of the
Court over one dialog: the judges' protocol (``court.judging``), the case drafts
the flare-ups opened, and the owner's decisions on them in the states of the Court
procedure (``docs/agents/logos-court-procedure.md``). A case starts as ``ALLEGED``;
every decision keeps its history. Filing a case to GitHub is the owner's action,
never automatic: the issue carries verdicts, rules and message numbers, the quotes
go to the encrypted evidence (``court.filing``).

The HTTP API is the blueprint ``court_bp`` (``/api/court``), the hall itself is
``static/court.js``.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

from flask import Blueprint, jsonify, request

from court import filing
from court.host import (
    add_message,
    bound,
    check_access,
    get_conversation_title,
    get_current_owner_id,
    get_messages,
    get_store,
    list_owned_conversations,
)
from court.judging import COURT_SOURCE, judge_dialog, rules_table

__all__ = [
    "DECISIONS",
    "close_hearing",
    "court_bp",
    "decide_case",
    "delete_hearing",
    "file_hearing_case",
    "get_hearing",
    "list_hearings",
    "open_hearing",
    "prepare_court_records",
    "rejudge_hearing",
    "reopen_hearing",
    "speak",
]

log = logging.getLogger("logos.court.hearings")

court_bp = Blueprint("court", __name__, url_prefix="/api/court")

PREFIX = "court/hearing/"
# The states of the Court procedure; ALLEGED until the owner decides.
DECISIONS = (
    "ALLEGED",
    "INVESTIGATING",
    "CONFIRMED",
    "REFUTED",
    "UNKNOWN",
    "FIXED",
    "VERIFIED",
    "ACCEPTED",
)


class HearingNotFound(LookupError):
    pass


def prepare_court_records() -> None:
    get_store()


def _owner() -> str | None:
    try:
        return get_current_owner_id(required=False)
    except bound.TreasuryIdentityError:
        return None


def _dialog_messages(conversation_id: str, owner: str | None) -> list[dict[str, Any]]:
    if not conversation_id or not check_access(conversation_id, owner):
        raise LookupError("conversation_not_found")
    return get_messages(conversation_id)


def _new_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        **case,
        "decision": "ALLEGED",
        "decision_reason": "Черновик заведён судом по вспышке; решения владельца нет",
        "decision_by": "UNASSIGNED",
        "history": [],
        "filed": None,
    }


def _key(hearing_id: str) -> str:
    return PREFIX + str(hearing_id)


def _load(hearing_id: str) -> dict[str, Any]:
    """Hearings are shared across the local installation, like the dialogs they sit over;
    a hearing outlives its dialog, so the dialog is not looked up here."""
    hearing = get_store().get(_key(hearing_id))
    if not isinstance(hearing, dict):
        raise HearingNotFound(hearing_id)
    return hearing


def _save(hearing: dict[str, Any]) -> dict[str, Any]:
    """Write back only the revision that was read: a concurrent change is not lost."""
    key = _key(hearing["id"])
    read_revision = int(hearing.get("revision") or 0)
    with get_store().transaction() as tx:
        current = tx.get(key)
        if current is None:
            raise HearingNotFound(hearing["id"])
        if int(current.get("revision") or 0) != read_revision:
            raise ValueError("заседание изменено одновременно; повторите")
        tx.set(
            key,
            {**hearing, "revision": read_revision + 1, "updated_at": int(time.time())},
        )
    return get_store().get(key)


def _summary(hearing: dict[str, Any]) -> dict[str, Any]:
    protocol = hearing["protocol"]
    return {
        "id": hearing["id"],
        "conversation_id": hearing["conversation_id"],
        "title": hearing["title"],
        "opened_at": hearing["opened_at"],
        "updated_at": hearing.get("updated_at"),
        "crisis": hearing["crisis"],
        "closure": hearing["closure"],
        "verdict": protocol["verdict"],
        "code": protocol["code"],
        "messages": protocol["messages"],
        "cases": len(hearing["cases"]),
        "flares": len(protocol["flares"]),
        "decisions": [case["decision"] for case in hearing["cases"]],
        "summary": protocol["summary"],
    }


def open_hearing(
    conversation_id: str, *, crisis: bool = False, opened_by: str | None = None
) -> dict[str, Any]:
    """Judge the dialog now and keep the sitting."""
    owner = opened_by if opened_by is not None else _owner()
    messages = _dialog_messages(conversation_id, owner)
    protocol = judge_dialog(messages, crisis=crisis)
    now = int(time.time())
    hearing = {
        "id": "hearing-" + uuid4().hex[:16],
        "conversation_id": str(conversation_id),
        "title": str(get_conversation_title(conversation_id, "") or ""),
        "opened_at": now,
        "updated_at": now,
        "opened_by": owner or "UNASSIGNED",
        "crisis": bool(crisis),
        "closure": "OPEN",
        "closure_reason": "",
        "protocol": protocol,
        "cases": [_new_case(case) for case in protocol["cases"]],
        "revision": 1,
    }
    with get_store().transaction() as tx:
        tx.set(_key(hearing["id"]), hearing)
    return hearing


def rejudge_hearing(hearing_id: str, *, crisis: bool | None = None) -> dict[str, Any]:
    """Judge the dialog again; a case keeps its decisions when its flare is still there."""
    owner = _owner()
    hearing = _load(hearing_id)
    if crisis is None:
        crisis = hearing["crisis"]
    messages = _dialog_messages(hearing["conversation_id"], owner)
    protocol = judge_dialog(messages, crisis=crisis)
    previous = {
        (case["flare"].get("message_id"), case["flare"]["n"]): case
        for case in hearing["cases"]
    }
    cases = []
    for case in protocol["cases"]:
        kept = previous.get((case["flare"].get("message_id"), case["flare"]["n"]))
        if kept is None:
            cases.append(_new_case(case))
            continue
        history = list(kept["history"])
        history.append(
            {
                "at": protocol["judged_at"],
                "previous": kept["decision"],
                "decision": kept["decision"],
                "reason": "пересмотр: новый прогон судов, решение сохранено",
                "by": owner or "UNASSIGNED",
            }
        )
        cases.append(
            {
                **case,
                "decision": kept["decision"],
                "decision_reason": kept["decision_reason"],
                "decision_by": kept["decision_by"],
                "history": history,
                "filed": kept.get("filed"),
            }
        )
    hearing.update({"protocol": protocol, "cases": cases, "crisis": bool(crisis)})
    return _save(hearing)


def list_hearings() -> list[dict[str, Any]]:
    hearings = [
        hearing
        for hearing in get_store().items(prefix=PREFIX).values()
        if isinstance(hearing, dict)
    ]
    hearings.sort(key=lambda hearing: int(hearing.get("opened_at") or 0), reverse=True)
    return [_summary(hearing) for hearing in hearings]


def get_hearing(hearing_id: str) -> dict[str, Any]:
    return _load(hearing_id)


def _case(hearing: dict[str, Any], case_number: int) -> dict[str, Any]:
    for case in hearing["cases"]:
        if int(case["n"]) == int(case_number):
            return case
    raise LookupError("case_not_found")


def decide_case(
    hearing_id: str,
    case_number: int,
    decision: str,
    reason: str,
    *,
    by: str | None = None,
) -> dict[str, Any]:
    """The owner's decision on a case; the previous one stays in the history."""
    decision = str(decision or "").strip().upper()
    if decision not in DECISIONS:
        raise ValueError("decision must be one of " + ", ".join(DECISIONS))
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError(
            "decision_reason is required: every decision rests on a reason"
        )
    owner = _owner()
    hearing = _load(hearing_id)
    case = _case(hearing, case_number)
    author = by or owner or "UNASSIGNED"
    case["history"].append(
        {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "previous": case["decision"],
            "decision": decision,
            "reason": reason,
            "by": author,
        }
    )
    case.update(
        {"decision": decision, "decision_reason": reason, "decision_by": author}
    )
    return _save(hearing)


def file_hearing_case(hearing_id: str, case_number: int) -> dict[str, Any]:
    """File the case to GitHub (the owner's action) and remember the issue."""
    owner = _owner()
    hearing = _load(hearing_id)
    case = _case(hearing, case_number)
    if case.get("filed"):
        return hearing
    url = filing.file_hearing_case(hearing, case)
    case["filed"] = {"url": url, "at": int(time.time()), "by": owner or "UNASSIGNED"}
    return _save(hearing)


def close_hearing(hearing_id: str, reason: str) -> dict[str, Any]:
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("closure reason is required")
    hearing = _load(hearing_id)
    hearing.update({"closure": "CLOSED", "closure_reason": reason})
    return _save(hearing)


def reopen_hearing(hearing_id: str, reason: str) -> dict[str, Any]:
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("reopen reason is required")
    hearing = _load(hearing_id)
    hearing.update({"closure": "OPEN", "closure_reason": reason})
    return _save(hearing)


# --- the Court as a participant of the group chat -----------------------------


def _hearing_of(conversation_id: str) -> dict[str, Any] | None:
    """The open sitting over this dialog: the group chat is the sitting."""
    sittings = [
        hearing
        for hearing in get_store().items(prefix=PREFIX).values()
        if isinstance(hearing, dict)
        and hearing.get("conversation_id") == str(conversation_id)
        and hearing.get("closure") == "OPEN"
    ]
    sittings.sort(key=lambda hearing: int(hearing.get("opened_at") or 0))
    return sittings[-1] if sittings else None


def _merge_cases(
    hearing: dict[str, Any], protocol: dict[str, Any], by: str
) -> list[dict[str, Any]]:
    previous = {
        (case["flare"].get("message_id"), case["flare"]["n"]): case
        for case in hearing["cases"]
    }
    cases = []
    for case in protocol["cases"]:
        kept = previous.get((case["flare"].get("message_id"), case["flare"]["n"]))
        if kept is None:
            cases.append(_new_case(case))
            continue
        cases.append(
            {
                **case,
                "decision": kept["decision"],
                "decision_reason": kept["decision_reason"],
                "decision_by": kept["decision_by"],
                "history": kept["history"],
                "filed": kept.get("filed"),
            }
        )
    return cases


def _speech(protocol: dict[str, Any], since: int, asked_rules: bool) -> str:
    """What the Court says into the chat: the verdicts on the replies since its last word."""
    entries = [
        e
        for e in protocol["entries"]
        if e["n"] > since and e["role"] in ("agent", "user")
    ]
    lines = ["⚖️ **Суд Логоса**"]
    judged = [e for e in entries if e["role"] == "agent"]
    if not judged and not any(e.get("flare") for e in entries):
        lines.append(
            "С прошлого слова суда новых реплик агента не было; судить нечего (код 2)."
        )
    for entry in entries:
        if entry.get("flare"):
            lines.append(
                f"- #{entry['n']} пользователь — **вспышка** (S3.1): «{entry['flare']}»"
            )
            continue
        if entry["role"] != "agent":
            continue
        head = f"- #{entry['n']} агент — **{entry['verdict']}**"
        if not entry["findings"]:
            lines.append(head + ": нарушений по записи не найдено")
            continue
        lines.append(head)
        for finding in entry["findings"]:
            quote = f" — «{finding['quote']}»" if finding["quote"] else ""
            lines.append(
                f"  - {finding['verdict']} {finding['rule']}: {finding['reason']}{quote}"
            )
    new_cases = [c for c in protocol["cases"] if c["flare"]["n"] > since]
    for case in new_cases:
        lines.append(
            f"- **Дело {case['n']}** ({case['verdict']}): вспышка в #{case['flare']['n']}, "
            f"в окне вины {len(case['items'])} акт(ов): "
            + ", ".join(f"#{i['n']} {i['rule']} {i['verdict']}" for i in case["items"])
            + ". Решение — за владельцем (ALLEGED)."
        )
    for flare in protocol["flares"]:
        if flare["n"] > since and flare["case"] is None:
            lines.append(f"- Вспышка #{flare['n']}: {flare['decision']}.")
    summary = protocol["summary"]
    lines.append(
        f"Итог по диалогу: **{protocol['verdict'] or '—'}**, код {protocol['code']}; "
        + " · ".join(f"{k} {v}" for k, v in summary.items())
        + f"; дел {len(protocol['cases'])}."
    )
    if asked_rules:
        lines.append("")
        lines.append("Правила суда:")
        for rule in rules_table():
            lines.append(
                f"- {rule['rule']} ({rule['judge']}, {rule['verdict'] or '—'}): {rule['norm']}"
            )
    else:
        lines.append(
            "Суд судит по записи, не по намерению; каждый вердикт — цитата и номер правила. "
            "Спросите суд о правилах, и он их перечислит."
        )
    return "\n".join(lines)


def speak(conversation_id: str, *, crisis: bool | None = None) -> dict[str, Any]:
    """The Court's turn in the group chat: judge what was said since its last word and say it.

    The sitting over the dialog is opened on the first word and kept up to date after."""
    owner = _owner()
    messages = _dialog_messages(conversation_id, owner)
    hearing = _hearing_of(conversation_id)
    if crisis is None:
        crisis = bool(hearing["crisis"]) if hearing else False
    since = 0
    for n, message in enumerate(messages, start=1):
        if message.get("source") == COURT_SOURCE:
            since = n
    asked_rules = False
    for message in reversed(messages):
        if str(message.get("role") or "").lower() == "user":
            asked_rules = "правил" in str(message.get("text") or "").lower()
            break
    protocol = judge_dialog(messages, crisis=crisis)
    text = _speech(protocol, since, asked_rules)
    message_id = add_message(
        str(conversation_id), "assistant", text, source=COURT_SOURCE
    )
    if hearing is None:
        now = int(time.time())
        hearing = {
            "id": "hearing-" + uuid4().hex[:16],
            "conversation_id": str(conversation_id),
            "title": str(get_conversation_title(conversation_id, "") or ""),
            "opened_at": now,
            "updated_at": now,
            "opened_by": owner or "UNASSIGNED",
            "crisis": bool(crisis),
            "closure": "OPEN",
            "closure_reason": "",
            "protocol": protocol,
            "cases": [_new_case(case) for case in protocol["cases"]],
            "revision": 1,
        }
        with get_store().transaction() as tx:
            tx.set(_key(hearing["id"]), hearing)
    else:
        hearing.update(
            {
                "protocol": protocol,
                "cases": _merge_cases(hearing, protocol, owner or "UNASSIGNED"),
                "crisis": bool(crisis),
            }
        )
        hearing = _save(hearing)
    return {"message_id": message_id, "text": text, "hearing": hearing}


def delete_hearing(hearing_id: str) -> None:
    hearing = _load(hearing_id)
    with get_store().transaction() as tx:
        tx.delete(_key(hearing["id"]))


# --- HTTP --------------------------------------------------------------------


def _body() -> dict[str, Any]:
    data = request.get_json(silent=True) or {}
    return data if isinstance(data, dict) else {}


def _error(exc: Exception) -> tuple[Any, int]:
    if isinstance(exc, HearingNotFound):
        return jsonify({"error": "hearing_not_found"}), 404
    if isinstance(exc, LookupError):
        return jsonify({"error": str(exc) or "not_found"}), 404
    if isinstance(exc, ValueError):
        return jsonify({"error": "invalid_request", "message": str(exc)}), 400
    log.exception("court hall request failed")
    return jsonify(
        {"error": "court_failed", "message": f"{type(exc).__name__}: {exc}"}
    ), 500


@court_bp.get("/rules")
def api_rules():
    return jsonify({"rules": rules_table(), "decisions": list(DECISIONS)})


@court_bp.get("/dialogs")
def api_dialogs():
    """Dialogs the hall can sit over: the owner's own."""
    owner = _owner()
    return jsonify({"conversations": list_owned_conversations(owner)})


@court_bp.post("/judge")
def api_judge():
    """The protocol alone, nothing kept: a dialog by id or the messages themselves."""
    data = _body()
    try:
        if "messages" in data:
            messages = data["messages"]
            if not isinstance(messages, list):
                raise ValueError("messages must be a list")
        else:
            messages = _dialog_messages(
                str(data.get("conversation_id") or ""), _owner()
            )
        return jsonify(
            {"protocol": judge_dialog(messages, crisis=bool(data.get("crisis")))}
        )
    except Exception as exc:
        return _error(exc)


@court_bp.post("/speak")
def api_speak():
    """The Court's turn in the group chat of a dialog."""
    data = _body()
    try:
        crisis = data.get("crisis")
        return jsonify(
            speak(
                str(data.get("conversation_id") or ""),
                crisis=None if crisis is None else bool(crisis),
            )
        )
    except Exception as exc:
        return _error(exc)


@court_bp.get("/hearings")
def api_hearings():
    return jsonify({"hearings": list_hearings()})


@court_bp.post("/hearings")
def api_open_hearing():
    data = _body()
    try:
        hearing = open_hearing(
            str(data.get("conversation_id") or ""), crisis=bool(data.get("crisis"))
        )
        return jsonify({"hearing": hearing}), 201
    except Exception as exc:
        return _error(exc)


@court_bp.get("/hearings/<hearing_id>")
def api_hearing(hearing_id: str):
    try:
        return jsonify({"hearing": get_hearing(hearing_id)})
    except Exception as exc:
        return _error(exc)


@court_bp.delete("/hearings/<hearing_id>")
def api_delete_hearing(hearing_id: str):
    try:
        delete_hearing(hearing_id)
        return jsonify({"deleted": hearing_id})
    except Exception as exc:
        return _error(exc)


@court_bp.post("/hearings/<hearing_id>/rejudge")
def api_rejudge(hearing_id: str):
    data = _body()
    try:
        crisis = data.get("crisis")
        return jsonify(
            {
                "hearing": rejudge_hearing(
                    hearing_id, crisis=None if crisis is None else bool(crisis)
                )
            }
        )
    except Exception as exc:
        return _error(exc)


@court_bp.post("/hearings/<hearing_id>/cases/<int:case_number>/decision")
def api_decide(hearing_id: str, case_number: int):
    data = _body()
    try:
        return jsonify(
            {
                "hearing": decide_case(
                    hearing_id, case_number, data.get("decision"), data.get("reason")
                )
            }
        )
    except Exception as exc:
        return _error(exc)


@court_bp.post("/hearings/<hearing_id>/cases/<int:case_number>/file")
def api_file_case(hearing_id: str, case_number: int):
    try:
        return jsonify({"hearing": file_hearing_case(hearing_id, case_number)})
    except Exception as exc:
        return _error(exc)


@court_bp.post("/hearings/<hearing_id>/close")
def api_close(hearing_id: str):
    try:
        return jsonify({"hearing": close_hearing(hearing_id, _body().get("reason"))})
    except Exception as exc:
        return _error(exc)


@court_bp.post("/hearings/<hearing_id>/reopen")
def api_reopen(hearing_id: str):
    try:
        return jsonify({"hearing": reopen_hearing(hearing_id, _body().get("reason"))})
    except Exception as exc:
        return _error(exc)
