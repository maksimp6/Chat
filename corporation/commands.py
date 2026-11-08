"""The owner's steering lines in the chat: "корпорация: …".

The board does what a board does and nothing more (owner, 08.11.2026): direction,
decisions, money the channels did not see, the switches. Every command is a ledger
record; the reply is text for the chat, no model is asked.
"""

from __future__ import annotations

import re
from typing import Any

from corporation import cash, cycle
from corporation import ledger as journal

__all__ = ["is_command", "handle"]

PREFIX = re.compile(r"^\s*корпорация\s*[:：]\s*", re.IGNORECASE)

HELP = (
    "Команды правления:\n"
    "• корпорация: старт — цикл работает (это и есть разрешение на автоматическую работу)\n"
    "• корпорация: стоп — остановить на границе хода\n"
    "• корпорация: директива <текст> — что делаем\n"
    "• корпорация: решение <проект|задача> <close|requeue|keep> <текст>\n"
    "• корпорация: продано <продукт> <сумма> [покупатель] [примечание]\n"
    "• корпорация: расход <сумма> <на что>\n"
    "• корпорация: канал telegram|email вкл|выкл\n"
    "• корпорация: статус — положение дел\n"
    "• корпорация: снимок — резервная копия сейчас\n"
    "• корпорация: журнал [n] — последние записи"
)


def is_command(message: Any) -> bool:
    return isinstance(message, str) and bool(PREFIX.match(message))


def _fmt_money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def _status_text() -> str:
    st = journal.state()
    money = cash.summary(st)
    cyc = st.cycle
    lines = [
        f"Цикл: {'работает' if cyc.get('running') else 'остановлен'}; шагов {cyc.get('steps', 0)}, последний ход: {cyc.get('last_tick') or '—'} ({cyc.get('last_role') or '—'}).",
        f"Касса: подтверждено {_fmt_money(money['confirmed'])} ₽, предложено {_fmt_money(money['proposed'])} ₽, расходы {_fmt_money(money['expenses'])} ₽, итог {_fmt_money(money['net'])} ₽.",
        f"Директив {len(st.directives)} (без проекта {sum(1 for d in st.directives if not d.get('project'))}); проектов открыто {sum(1 for p in st.projects.values() if p['status']=='open')}.",
        "Задачи: " + ", ".join(f"{k} {v}" for k, v in st.summary()["tasks"].items()) + ".",
        "Продукты: " + ", ".join(f"{k} {v}" for k, v in st.summary()["products"].items())
        + "; офферы: " + ", ".join(f"{k} {v}" for k, v in st.summary()["offers"].items()) + ".",
        "Каналы: " + (", ".join(f"{name} {'вкл' if c.get('enabled') else 'выкл'}" for name, c in st.channels.items()) or "нет") + ".",
        f"Записей в журнале: {st.records}; последний снимок: {(st.last_backup or {}).get('at') or '—'}.",
    ]
    return "\n".join(lines)


def _journal_text(count: int) -> str:
    rows = []
    for entry in journal.ledger().tail(count):
        data = entry.get("data") or {}
        what = data.get("text") or data.get("reason") or data.get("result") or data.get("reply") or data.get("title") or data.get("error") or ""
        rows.append(f"{entry['seq']} {entry['at'][11:19]} {entry['kind']} [{entry.get('actor')}] {str(what)[:140]}")
    return "\n".join(rows) or "Журнал пуст."


def handle(message: str) -> str:
    """The reply to one command line."""
    rest = PREFIX.sub("", message).strip()
    if not rest:
        return HELP
    word, _, tail = rest.partition(" ")
    word = word.lower()
    tail = tail.strip()
    book = journal.ledger()
    book.append("board.command", "owner", {"text": rest[:2000]})
    if word in ("старт", "start"):
        result = cycle.start(message.strip())
        return "Цикл запущен. " + ("Поток работает." if result["thread_alive"] else "Поток не поднялся: смотри журнал.") + "\n" + _status_text()
    if word in ("стоп", "stop"):
        cycle.stop("owner")
        return "Цикл остановится на границе хода.\n" + _status_text()
    if word in ("директива", "directive"):
        if not tail:
            return "Директива пуста. Пример: корпорация: директива делаем и продаём чек-листы для малого бизнеса в Telegram."
        entry = book.append("board.directive", "owner", {"text": tail[:4000]})
        return f"Директива записана ([{entry['seq']}]). Директор возьмёт её на следующем шаге цикла."
    if word in ("решение", "decision"):
        parts = tail.split(" ", 2)
        if len(parts) < 2:
            return "Формат: корпорация: решение <проект|задача> <close|requeue|keep> <текст>"
        target, verdict = parts[0], parts[1].lower()
        text = parts[2] if len(parts) > 2 else ""
        if verdict not in ("close", "requeue", "keep"):
            return "Вердикт: close (закрыть проект), requeue (вернуть задачу в очередь) или keep."
        entry = book.append("board.decision", "owner", {"target": target, "verdict": verdict, "text": text[:2000]})
        return f"Решение записано ([{entry['seq']}]): {target} → {verdict}."
    if word in ("продано", "sold"):
        parts = tail.split(" ", 3)
        if len(parts) < 2:
            return "Формат: корпорация: продано <продукт> <сумма> [покупатель] [примечание]"
        try:
            amount = float(parts[1].replace(",", "."))
        except ValueError:
            return "Сумма должна быть числом."
        client = parts[2] if len(parts) > 2 else None
        note = parts[3] if len(parts) > 3 else ""
        try:
            made = cash.confirm_sale(parts[0], amount, note, client)
        except ValueError as exc:
            return f"Не записано: {exc}"
        return f"Продажа записана ([{made['seq']}]): {parts[0]} за {_fmt_money(amount)} ₽. Подтверждено всего: {_fmt_money(made['confirmed'])} ₽."
    if word in ("расход", "expense"):
        parts = tail.split(" ", 1)
        try:
            amount = float(parts[0].replace(",", "."))
        except (ValueError, IndexError):
            return "Формат: корпорация: расход <сумма> <на что>"
        made = cash.expense(amount, parts[1] if len(parts) > 1 else "")
        return f"Расход записан ([{made['seq']}]): {_fmt_money(amount)} ₽."
    if word in ("канал", "channel"):
        parts = tail.split()
        if len(parts) < 2 or parts[0].lower() not in ("telegram", "email"):
            return "Формат: корпорация: канал telegram|email вкл|выкл"
        name, switch = parts[0].lower(), parts[1].lower()
        if switch in ("вкл", "on"):
            from corporation import channels

            keys = channels.configured().get(name)
            book.append("channel.start", "owner", {"name": name})
            return f"Канал {name} включён." + ("" if keys else f" Ключей канала в core/.env нет: продажи через {name} откажут, пока их не положить.")
        book.append("channel.stop", "owner", {"name": name, "reason": "owner"})
        return f"Канал {name} выключен."
    if word in ("статус", "status"):
        return _status_text()
    if word in ("снимок", "snapshot", "backup"):
        made = book.snapshot()
        book.append("backup", "owner", made)
        return f"Снимок {made['stamp']}: {len(made['targets'])} копии ({', '.join(made['targets'])}); записей {made['records']}, цепочка {'цела' if made['ok'] else 'ПОВРЕЖДЕНА'}."
    if word in ("журнал", "ledger", "log"):
        try:
            count = int(tail) if tail else 20
        except ValueError:
            count = 20
        return _journal_text(max(1, min(count, 200)))
    return "Не понял команду.\n" + HELP
