"""Onboarding: every employee writes their own personnel file; the Court checks the set.

Owner, 09.11.2026: "do onboarding, collect cv and write workers личное дело, запиши его
эмоциональное состояние и готовность к работе, свои результаты сверь у суда" — and, in
the same breath, "dont do work instead of them". So nothing here writes a file *for* an
employee. The onboarding is one turn in the employee's own office dialog, where the
employee answers with a ``corporation personnel`` call (cv, mood, readiness): the record
is theirs — actor = the role — and it is embedded like every ledger record (``staff.file``).
The file is also a file, ``<state>/corporation/staff/<role>.md`` (owner, 09.11.2026: the
embeddings database "is not replace files manager"); the record carries its path.
An employee who ends the turn without that call gets a ``note`` saying so, with their own
words quoted; no file is invented for them.

When every office has had its turn, the whole set goes before the Court (the Court dialog,
``logos-court``): what each file says, who has none. The Court's verdict is a ``note`` of
actor ``court`` with ``check: onboarding``.

The turns run in one thread, one office after another (the system is synchronous). The
chat command ``корпорация: онбординг [роль]`` starts it; ``корпорация: дело <роль>`` reads
a file; the board window reads ``POST /api/corporation {action: staff}``.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from corporation import ledger as journal
from corporation.roles import ROLES

logger = logging.getLogger("logos.corporation.staff")

__all__ = ["file_text", "onboard", "running"]

_thread: threading.Thread | None = None
_lock = threading.Lock()

# A low round cap for the onboarding turn (owner, 09.11.2026: "fix"). The office turn
# otherwise runs at WATCHDOG_ROUNDS (20): a weak model (the 4B) then re-calls the same
# tool again and again — on 09.11 one role wrote its file 21 times in a single turn. The
# onboarding is one tool call plus one sentence, so a cap of 4 is generous and stops the
# loop. The office route keeps a caller's max_rounds (chat.mcp_routes); the cycle is
# untouched. This is not a business limit — it bounds one onboarding turn only.
ONBOARD_ROUNDS = 4


def running() -> bool:
    return bool(_thread is not None and _thread.is_alive())


def _brief(role: str) -> str:
    from corporation.roles import DEFAULT_GOAL

    st = journal.state()
    return (
        f"Онбординг. Ты — {ROLES[role].title} в компании мобильных приложений. "
        f"Цель компании: {st.goal or DEFAULT_GOAL}\n\n"
        "Сделай ровно одно действие: вызови инструмент corporation с tool=personnel и "
        "arguments с тремя полями — cv (кто ты и чем полезен компании мобильных приложений), "
        "mood (твоё эмоциональное состояние сейчас, своими словами), readiness (готов ли ты к "
        "работе и почему). Затем напиши одно короткое предложение. Других инструментов "
        "(status, git, filesystem) не вызывай: нужен только personnel. Личное дело пишешь ты сам."
    )


def _retry_brief(role: str) -> str:
    return (
        f"Ты — {ROLES[role].title}. Личное дело ещё не записано. Сейчас вызови инструмент "
        'corporation ровно один раз: tool=personnel, arguments={"cv": "…", "mood": "…", '
        '"readiness": "…"} — заполни поля своими словами про себя как сотрудника компании '
        "мобильных приложений. Не вызывай status, git, filesystem и другие инструменты. "
        "После вызова напиши одно предложение."
    )


def _turn(conversation: str, message: str, source: str, max_rounds: int = 0) -> str:
    from corporation.cycle import _local

    body: dict[str, Any] = {
        "conversation_id": conversation,
        "message": message,
        "source": source,
    }
    if max_rounds:
        body["params"] = {"max_rounds": max_rounds}
    answer = _local("POST", "/api/chat", body, timeout=3600)
    return str(answer.get("reply") or "")


def _onboard_one(role: str) -> dict[str, Any]:
    """One employee's onboarding turn in their office; True in ``recorded`` only when the
    employee's own ``personnel`` call landed in the ledger during the turn.

    A tool-only turn that ends without a text reply makes the chat pipeline answer HTTP 500
    (_EmptyModelReply), and a weak model sometimes reaches for the wrong tool (status) and
    never calls personnel. So the turn is capped at ONBOARD_ROUNDS and, if no file landed,
    retried once with a blunter brief; both are survivable — the file lands from the tool
    call even when the turn then 500s on the empty reply (we check the ledger, not the text).
    """
    from corporation import office

    conversation = office.ensure_office(role)
    source = f"corporation:onboarding:{role}"
    book = journal.ledger()
    book.append(
        "note",
        "owner",
        {
            "text": f"онбординг: {ROLES[role].title} ({role}) — ход в кабинете",
            "onboarding": role,
            "conversation": conversation,
        },
    )
    reply, error, recorded = "", True, False
    for brief in (_brief(role), _retry_brief(role)):
        before = journal.state().staff.get(role)
        try:
            reply = _turn(conversation, brief, source, ONBOARD_ROUNDS)
            error = reply.startswith("⚠️")
        except Exception as exc:
            reply, error = f"{type(exc).__name__}: {exc}", True
        after = journal.state().staff.get(role)
        recorded = bool(after and (before is None or after["seq"] != before["seq"]))
        if recorded:
            error = False
            break
    if not recorded:
        book.append(
            "note",
            "owner",
            {
                "text": f"онбординг: {role} закончил оба хода без corporation personnel — личное дело не заведено",
                "onboarding": role,
                "reply": reply[:2000],
                "error": error,
            },
        )
    return {
        "role": role,
        "conversation": conversation,
        "recorded": recorded,
        "error": error,
        "reply": reply[:500],
    }


def _court_check(results: list[dict[str, Any]]) -> str:
    """The set of files before the Court; its verdict is a note of actor ``court``."""
    from conversations.routes import COURT_CONVERSATION_ID

    st = journal.state()
    lines = [
        "Сверка онбординга компании мобильных приложений. Проверь по журналу компании "
        "(инструмент corporation status; записи staff.file): у каждого сотрудника есть личное дело, "
        "записанное им самим (actor — его роль), поля cv, mood и readiness заполнены по делу, без "
        "служебных данных и выдумок; у кого дела нет — почему. Вынеси вердикт по каждому и общий.",
        "",
    ]
    for result in results:
        role = result["role"]
        item = st.staff.get(role)
        if item:
            lines.append(
                f"- {role} ({ROLES[role].title}): дело, запись {item['seq']}; cv: {item['cv'][:300]}; "
                f"mood: {item['mood'][:200]}; readiness: {item['readiness'][:200]}"
            )
        else:
            lines.append(
                f"- {role} ({ROLES[role].title}): дела нет; ход закончился словами: {result['reply'][:300]}"
            )
    try:
        verdict = _turn(
            COURT_CONVERSATION_ID,
            "\n".join(lines),
            "corporation:onboarding:court",
            max_rounds=8,
        )
    except Exception as exc:
        verdict = f"сверка не вынесена: ход Суда не удался ({type(exc).__name__}: {exc})"
    journal.ledger().append(
        "note",
        "court",
        {
            "text": verdict[:4000],
            "check": "onboarding",
            "roles": " ".join(r["role"] for r in results),
            "recorded": " ".join(r["role"] for r in results if r["recorded"]),
        },
    )
    return verdict


def _run(roles: list[str]) -> None:
    logger.info("[CORP] onboarding runs: %s", roles)
    results: list[dict[str, Any]] = []
    for role in roles:
        try:
            results.append(_onboard_one(role))
        except Exception as exc:
            logger.exception("[CORP] onboarding of %s failed", role)
            results.append(
                {
                    "role": role,
                    "conversation": None,
                    "recorded": False,
                    "error": True,
                    "reply": f"{type(exc).__name__}: {exc}",
                }
            )
    try:
        _court_check(results)
    except Exception as exc:
        logger.exception("[CORP] the Court check of the onboarding failed")
        try:
            journal.ledger().append(
                "cycle.error",
                "court",
                {"error": f"onboarding court check: {type(exc).__name__}: {exc}"},
            )
        except Exception:
            pass
    logger.info("[CORP] onboarding done")


def onboard(role: str | None = None) -> dict[str, Any]:
    """Start the onboarding of one role or of every employee, in the offices' order."""
    global _thread
    if role is not None and role not in ROLES:
        raise ValueError(f"no such employee: {role}")
    from corporation.office import OFFICES

    roles = [role] if role else [r for r, _conversation, _title in OFFICES]
    with _lock:
        if _thread is not None and _thread.is_alive():
            return {"started": False, "reason": "онбординг уже идёт", "roles": roles}
        _thread = threading.Thread(
            target=_run, args=(roles,), name="logos-onboarding", daemon=True
        )
        _thread.start()
    return {"started": True, "roles": roles}


def file_text(role: str) -> str:
    """An employee's personnel file as a chat reply."""
    if role not in ROLES:
        raise ValueError(f"no such employee: {role}")
    item = journal.state().staff.get(role)
    if not item:
        return f"Личного дела {ROLES[role].title} ({role}) в журнале нет."
    text = (
        f"Личное дело {ROLES[role].title} ({role}), запись {item['seq']} от {item['at']} "
        f"(версия {item['versions']}):\nРезюме: {item['cv']}\nСостояние: {item['mood']}\n"
        f"Готовность: {item['readiness']}"
    )
    if item.get("notes"):
        text += f"\nПримечания: {item['notes']}"
    if item.get("path"):
        text += f"\nФайл: {item['path']}"
    return text
