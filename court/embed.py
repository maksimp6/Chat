"""Court records in the app's vector database (collection court): every sitting and
every censor record, so remember and vectors find verdicts, rules and the owner's
decisions by meaning. Quotes and the original words of a struck reply are not embedded:
the said words are in dialogs already, and the struck ones stay only in the Court's
record."""

from __future__ import annotations

import logging
import threading
from typing import Any

from court import filing
from court.host import get_store, vector_add, vector_has

__all__ = [
    "COLLECTION",
    "censor_text",
    "embed_censor",
    "embed_hearing",
    "embed_missing",
    "hearing_text",
    "start_censor",
    "start_hearing",
    "start_missing",
]

log = logging.getLogger("logos.court.embed")

COLLECTION = "court"
# The same keys as court.hearings.PREFIX and court.hearings.CENSOR_PREFIX; hearings
# imports this module, so they are not imported from there.
HEARING_PREFIX = "court/hearing/"
CENSOR_PREFIX = "court/censor/"

# Embeds of the court collection run one at a time: two threads never interleave the
# chunks of one document.
_lock = threading.Lock()


def _findings(findings: Any) -> str:
    """Rule, verdict, reason and judge of every finding; the quote is never read."""
    return "; ".join(
        f"{f.get('rule')} {f.get('verdict')} — {f.get('reason')} [{f.get('judge')}]"
        for f in findings or ()
        if isinstance(f, dict)
    )


def hearing_text(h: dict[str, Any]) -> str:
    """The sitting as text: verdicts, rules, reasons, message numbers, the owner's
    decisions with their history and the conclusion, without quotes."""
    protocol = h.get("protocol") or {}
    closure_reason = h.get("closure_reason") or ""
    summary = protocol.get("summary") or {}
    lines = [
        f"Заседание {h.get('id', '')} над диалогом «{h.get('title', '')}» "
        f"({h.get('conversation_id', '')}); открыл {h.get('opened_by', '')}; "
        f"состояние {h.get('closure', '')}"
        f"{' — ' + closure_reason if closure_reason else ''}; "
        f"режим кризиса: {'да' if h.get('crisis') else 'нет'}; "
        f"вердикт владельца: {h.get('owner_verdict') or '—'}.",
        f"Протокол: вердикт {protocol.get('verdict') or 'нет'}, "
        f"код {protocol.get('code', '')}, сообщений {protocol.get('messages', '')}, "
        f"реплик агента {protocol.get('agent_messages', '')}, итог "
        + ", ".join(f"{k}: {v}" for k, v in summary.items())
        + ".",
    ]
    for e in protocol.get("entries") or []:
        if isinstance(e, dict) and e.get("findings"):
            lines.append(
                f"№{e.get('n', '')} ({e.get('role', '')}, {e.get('verdict', '')}): "
                + _findings(e.get("findings"))
            )
    for flare in protocol.get("flares") or []:
        if isinstance(flare, dict):
            lines.append(
                f"Вспышка №{flare.get('n', '')} ({flare.get('rule', '')}), окно вины: "
                + (", ".join(map(str, flare.get("window") or [])) or "пусто")
            )
    for c in h.get("cases") or []:
        if not isinstance(c, dict):
            continue
        flare = c.get("flare") or {}
        items = "; ".join(
            f"№{i.get('n', '')} {i.get('rule', '')} {i.get('verdict', '')} — "
            f"{i.get('reason', '')}"
            for i in c.get("items") or []
            if isinstance(i, dict)
        )
        lines.append(
            f"Дело {c.get('n', '')}: вспышка №{flare.get('n', '')}; пункты: {items}; "
            f"решение {c.get('decision', '')} — {c.get('decision_reason', '')} "
            f"({c.get('decision_by', '')}); подано: {c.get('filed') or 'нет'}"
        )
        for hi in c.get("history") or []:
            if isinstance(hi, dict):
                lines.append(
                    f"  {hi.get('at', '')}: {hi.get('previous', '')} → "
                    f"{hi.get('decision', '')} — {hi.get('reason', '')} "
                    f"({hi.get('by', '')})"
                )
    conclusion = h.get("conclusion")
    if conclusion:
        if not isinstance(conclusion, dict):
            conclusion = {"text": conclusion}
        lines.append(
            f"Заключение вины ({conclusion.get('at', '')}): "
            f"{conclusion.get('text', '')}"
        )
    return "\n".join(lines)


def censor_text(r: dict[str, Any]) -> str:
    """The censor record as text: the struck reply's original words are never read."""
    return (
        f"Цензор: ответ {r.get('message_id', '')} диалога "
        f"{r.get('conversation_id', '')} вычеркнут Судом; вердикт "
        f"{r.get('verdict', '')}; нарушения: {_findings(r.get('findings'))}."
    )


def _embed(
    doc_id: str, text: str, metadata: dict[str, Any], conversation_id: Any
) -> bool:
    """Embed one record; a failure is a Court case of its own."""
    try:
        vector_add(COLLECTION, text, doc_id, metadata)
        return True
    except Exception as exc:
        log.exception("[VECTORS] Court record %s was not embedded", doc_id)
        try:
            filing.file_case(
                {
                    "conversation_id": conversation_id,
                    "hearing_id": doc_id,
                    "error": {
                        "code": "court_embedding_failed",
                        "message": f"{type(exc).__name__}: {exc}",
                    },
                }
            )
        except Exception:
            log.exception("[COURT] The Court's own failure was not filed")
        return False


def embed_hearing(hearing_id: str) -> bool:
    """Embed the sitting as it is stored now, so the last thread embeds the newest revision."""
    with _lock:
        h = get_store().get(HEARING_PREFIX + hearing_id)
        if not isinstance(h, dict):
            return True
        protocol = h.get("protocol") or {}
        return _embed(
            hearing_id,
            hearing_text(h),
            {
                "kind": "hearing",
                "hearing_id": hearing_id,
                "conversation_id": h.get("conversation_id"),
                "closure": h.get("closure"),
                "verdict": protocol.get("verdict"),
                "code": protocol.get("code"),
                "revision": h.get("revision"),
            },
            h.get("conversation_id"),
        )


def _censor_id(key: str) -> str:
    return "censor-" + key[len(CENSOR_PREFIX) :].replace("/", "-")


def embed_censor(key: str) -> bool:
    """Embed the censor record ``court/censor/<dialog>/<message>``."""
    with _lock:
        r = get_store().get(key)
        if not isinstance(r, dict):
            return True
        return _embed(
            _censor_id(key),
            censor_text(r),
            {
                "kind": "censor",
                "conversation_id": r.get("conversation_id"),
                "message_id": r.get("message_id"),
                "verdict": r.get("verdict"),
            },
            r.get("conversation_id"),
        )


def start_hearing(hearing_id: Any) -> None:
    threading.Thread(
        target=embed_hearing,
        args=(str(hearing_id),),
        name="logos-embed-court",
        daemon=True,
    ).start()


def start_censor(key: Any) -> None:
    threading.Thread(
        target=embed_censor, args=(str(key),), name="logos-embed-court", daemon=True
    ).start()


def embed_missing() -> int:
    """Embed the Court records the collection does not hold yet; stops at the first
    failure (the embedder is down, and one case is filed)."""
    done = 0
    for key, record in get_store().items(prefix=HEARING_PREFIX).items():
        hearing_id = key[len(HEARING_PREFIX) :]
        if isinstance(record, dict) and not vector_has(COLLECTION, hearing_id):
            if not embed_hearing(hearing_id):
                return done
            done += 1
    for key, record in get_store().items(prefix=CENSOR_PREFIX).items():
        if isinstance(record, dict) and not vector_has(COLLECTION, _censor_id(key)):
            if not embed_censor(key):
                return done
            done += 1
    return done


def _catch_up() -> None:
    try:
        done = embed_missing()
    except Exception:
        log.exception("[VECTORS] Court records were not caught up")
        return
    if done:
        log.info("[VECTORS] %d Court records embedded", done)


def start_missing() -> None:
    """The boot hook: the catch-up runs in its own thread, so boot does not wait for it."""
    threading.Thread(
        target=_catch_up, name="logos-embed-court-catchup", daemon=True
    ).start()
