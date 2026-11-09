"""The factory as a cartoon draws it: one belt, stations in a row, a bin of blanks
before each machine, the inspector with a loupe, a chute for rejects, the warehouse,
the shipping dock and the cash register at the end; a whistle while the shift is
on and smoke from the stack for every step made.

Owner, 08.11.2026: "по типу конвейера давай строить — посмотри как в мультиках
описывают заводы". Nothing here is stored: the picture is derived from the ledger's
state every time (``board``), the same way the cycle picks its next step, so what
the window shows is what the cycle sees.

The belt carries blanks (tasks) and parts (products):

    💡 Идеи ─► 🗂️ Контора ─► 🔭 Разведка ─► ⚙️ Цех ─► 🔍 ОТК ─► 🏷️ Витрина ─► 📦 Отгрузка ─► 💰 Касса
                                             ▲          │ брак (rework) назад в цех
                                             └──────────┘   провал (failed) ─► жёлоб ─► Суд
"""

from __future__ import annotations

from typing import Any

from corporation import cash
from corporation import ledger as journal

__all__ = ["DEPARTMENTS", "STATIONS", "board"]

# The company's front as the owner's picture draws it (08.11.2026): departments as
# cards, each with its people (roles), what it does and whether it is at work now.
DEPARTMENTS: tuple[dict[str, Any], ...] = (
    {
        "id": "orchestrator",
        "name": "Главный оркестратор",
        "sign": "🧭",
        "roles": ("founder", "director"),
        "does": (
            "Хочет и решает, что делаем",
            "Режет цели на проекты и задачи",
            "Отчёт правлению в журнал",
        ),
    },
    {
        "id": "sales",
        "name": "Маркетинг и продажи",
        "sign": "📣",
        "roles": ("marketing", "sales"),
        "does": ("Офферы и цены", "Витрина: ВК, Telegram, почта", "Счета и отгрузка"),
    },
    {
        "id": "dev",
        "name": "Разработка и продукты",
        "sign": "⚙️",
        "roles": ("production",),
        "does": (
            "Приложения для Android (исходники)",
            "Мобильные веб-приложения (PWA)",
            "Проект целиком: манифест, экраны, README",
        ),
    },
    {
        "id": "support",
        "name": "Поддержка клиентов",
        "sign": "🎧",
        "roles": ("sales",),
        "does": ("Ответы покупателям", "Доставка после оплаты", "Жалобы — в Суд"),
    },
    {
        "id": "finance",
        "name": "Финансы и бухгалтерия",
        "sign": "💰",
        "roles": (),
        "does": (
            "Касса из журнала",
            "Расходы и электричество",
            "Каждая копейка с хешем",
        ),
    },
    {
        "id": "quality",
        "name": "ОТК и Суд",
        "sign": "🔍",
        "roles": ("critic",),
        "does": (
            "Приёмка продуктов",
            "Ворота перед выходом наружу",
            "Дела для владельца",
        ),
    },
    {
        "id": "research",
        "name": "Аналитика и данные",
        "sign": "📊",
        "roles": ("research",),
        "does": ("Что продаётся и кому", "Память по смыслу", "Журнал в эмбеддингах"),
    },
    {
        "id": "infra",
        "name": "IT-инфраструктура",
        "sign": "🖥️",
        "roles": (),
        "does": ("Ящик без ОС, 7 Вт", "Одна модель, один шаг", "Снимки на /sdcard"),
    },
)


def _today(st: journal.State) -> dict[str, Any]:
    """Today's numbers (UTC day), each counted from the ledger."""
    from datetime import UTC, datetime

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    revenue = sum(
        float(s.get("amount") or 0)
        for s in st.money["sales"]
        if str(s.get("at", "")).startswith(day)
    )
    sales = sum(1 for s in st.money["sales"] if str(s.get("at", "")).startswith(day))
    done = sum(
        1
        for t in st.tasks.values()
        if t["status"] == "done" and str(t.get("closed", "")).startswith(day)
    )
    failed = sum(
        1
        for t in st.tasks.values()
        if t["status"] == "failed" and str(t.get("closed", "")).startswith(day)
    )
    opened = sum(
        1 for t in st.tasks.values() if str(t.get("opened", "")).startswith(day)
    )
    clients = sum(
        1
        for c in st.clients.values()
        if any(str(m.get("at", "")).startswith(day) for m in c["messages"])
    )
    products = sum(
        1 for p in st.products.values() if str(p.get("at", "")).startswith(day)
    )
    total = done + failed
    return {
        "day": day,
        "revenue": revenue,
        "sales": sales,
        "tasks_done": done,
        "tasks_failed": failed,
        "tasks_opened": opened,
        "tasks_pct": round(100 * done / total) if total else None,
        "clients": clients,
        "products": products,
    }


def _month_growth(st: journal.State) -> float | None:
    """This month's confirmed revenue against last month's, in percent; None without a last month."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    this_key = now.strftime("%Y-%m")
    from datetime import timedelta

    last = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    this_sum = sum(
        float(s.get("amount") or 0)
        for s in st.money["sales"]
        if str(s.get("at", "")).startswith(this_key)
    )
    last_sum = sum(
        float(s.get("amount") or 0)
        for s in st.money["sales"]
        if str(s.get("at", "")).startswith(last)
    )
    if not last_sum:
        return None
    return round(100 * (this_sum - last_sum) / last_sum)


def _departments(st: journal.State, current: dict[str, Any]) -> list[dict[str, Any]]:
    from datetime import UTC, datetime

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    result = []
    for spec in DEPARTMENTS:
        roles = spec["roles"]
        queued = sum(
            1
            for t in st.tasks.values()
            if t["role"] in roles and t["status"] == "queued"
        )
        working = sum(
            1
            for t in st.tasks.values()
            if t["role"] in roles and t["status"] == "working"
        )
        done_today = sum(
            1
            for t in st.tasks.values()
            if t["role"] in roles
            and t["status"] == "done"
            and str(t.get("closed", "")).startswith(day)
        )
        active = current.get("role") in roles
        if spec["id"] == "finance":
            state = "считает" if st.cycle.get("running") else "ждёт"
        elif spec["id"] == "infra":
            state = "смена идёт" if st.cycle.get("running") else "смена не идёт"
        elif active:
            state = "у станка"
        elif queued or working:
            state = f"в очереди {queued + working}"
        else:
            state = "свободен" if st.cycle.get("running") else "ждёт гудка"
        from corporation.roles import ROLES

        people = [ROLES[r].person for r in roles if r in ROLES]
        facts = _facts_today(st, roles) if roles else []
        if spec["id"] == "finance":
            money = cash.summary(st)
            facts = [
                f"Подтверждено: {money['confirmed']:.0f} ₽",
                f"Предложено: {money['proposed']:.0f} ₽",
                f"Расходы: {money['expenses']:.0f} ₽ (ток {money['power_rub']:.0f} ₽)",
            ]
        elif spec["id"] == "infra":
            facts = [
                f"Шагов за всё время: {st.cycle.get('steps', 0)}",
                f"Записей в журнале: {st.records}",
                f"Снимок: {(st.last_backup or {}).get('at') or 'ещё не было'}",
            ]
        if active:
            badge = "В работе"
        elif st.cycle.get("running") and (queued or working):
            badge = "В очереди"
        elif facts and spec["id"] not in ("finance", "infra"):
            badge = "Готово"
        else:
            badge = "Ждёт" if st.cycle.get("running") else "Смена не идёт"
        result.append(
            {
                **spec,
                "roles": list(roles),
                "people": people,
                "agents": len(roles) or 1,
                "queued": queued,
                "working": working,
                "done_today": done_today,
                "active": active,
                "state": state,
                "facts": facts,
                "badge": badge,
            }
        )
    return result


_EVENT_WORDS = {
    "cycle.start": "Старт смены",
    "cycle.stop": "Смена остановлена",
    "board.directive": "Директива",
    "board.goal": "Новая цель",
    "board.decision": "Решение правления",
    "project.open": "Открыт проект",
    "project.close": "Закрыт проект",
    "task.open": "Задача поставлена",
    "task.start": "За работу",
    "task.done": "Сделано",
    "task.failed": "Провал",
    "product.draft": "Продукт в ОТК",
    "product.review": "Вердикт ОТК",
    "offer.draft": "Оффер",
    "offer.published": "Опубликовано",
    "client.message": "Написал покупатель",
    "client.reply": "Ответ покупателю",
    "invoice": "Счёт",
    "delivery": "Отгрузка",
    "revenue.confirmed": "Оплата получена",
    "expense": "Расход",
    "court.case": "Дело Суда",
    "channel.stop": "Канал остановлен",
    "channel.start": "Канал включён",
    "model.switch": "Смена модели",
    "backup": "Снимок",
    "cycle.error": "Сбой цикла",
    "note": "Заметка",
}
_QUIET = {"cycle.tick", "board.command", "channel.cursor", "task.turn"}


def _who(actor: str | None) -> str:
    from corporation.roles import ROLES

    role = ROLES.get(actor or "")
    return (
        role.person["name"]
        if role
        else {
            "owner": "Владелец",
            "cycle": "Конвейер",
            "court": "Суд",
            "cash": "Касса",
            "channels": "Прилавок",
        }.get(actor or "", actor or "")
    )


def _what(entry: dict[str, Any]) -> str:
    data = entry.get("data") or {}
    text = (
        data.get("text")
        or data.get("result")
        or data.get("reason")
        or data.get("title")
        or data.get("error")
        or ""
    )
    if entry["kind"] == "revenue.confirmed":
        text = f"{float(data.get('amount') or 0):.0f} ₽ за {data.get('product') or '—'}"
    elif entry["kind"] == "offer.draft":
        text = f"{data.get('id')} по {float(data.get('price') or 0):.0f} ₽, {data.get('channel')}"
    elif entry["kind"] == "product.review":
        text = f"{data.get('verdict')}: {str(data.get('notes') or '')[:80]}"
    elif entry["kind"] == "task.open":
        text = f"{data.get('id')} → {_who(data.get('role'))}: {str(data.get('text') or '')[:80]}"
    elif entry["kind"] == "model.switch":
        text = f"{data.get('from')} → {data.get('to')}"
    ref = entry.get("task") or entry.get("product") or entry.get("project") or ""
    return (
        f"{ref} " if ref and entry["kind"] not in ("task.open", "offer.draft") else ""
    ) + str(text)[:120]


def _timeline(st: journal.State, limit: int = 60) -> list[dict[str, Any]]:
    """Today's working day as a line: every event with its time and its person, newest last."""
    from datetime import UTC, datetime

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    rows = []
    for entry in journal.ledger().entries():
        if not entry["at"].startswith(day) or entry["kind"] in _QUIET:
            continue
        rows.append(
            {
                "at": entry["at"],
                "seq": entry["seq"],
                "kind": entry["kind"],
                "label": _EVENT_WORDS.get(entry["kind"], entry["kind"]),
                "who": _who(entry.get("actor")),
                "what": _what(entry),
            }
        )
    return rows[-limit:]


def _facts_today(
    st: journal.State, roles: tuple[str, ...], limit: int = 3
) -> list[str]:
    """What a department did today, newest first: the three check marks of its row."""
    from datetime import UTC, datetime

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    facts = []
    for entry in reversed(journal.ledger().entries()):
        if not entry["at"].startswith(day):
            break
        if (
            entry.get("actor") not in roles
            or entry["kind"] in _QUIET
            or entry["kind"] == "task.start"
        ):
            continue
        facts.append(
            f"{_EVENT_WORDS.get(entry['kind'], entry['kind'])}: {_what(entry)}"
        )
        if len(facts) >= limit:
            break
    return facts


def _avg_turn_seconds(st: journal.State) -> float | None:
    from datetime import UTC, datetime

    day = datetime.now(UTC).strftime("%Y-%m-%d")
    seconds = [
        float((e.get("data") or {}).get("seconds") or 0)
        for e in journal.ledger().entries()
        if e["kind"] == "task.turn" and e["at"].startswith(day)
    ]
    return round(sum(seconds) / len(seconds), 1) if seconds else None


def _latest_report(st: journal.State) -> str | None:
    """The orchestrator's last word to the board: the newest note of the founder or director."""
    for note in reversed(st.notes):
        if note.get("actor") in ("founder", "director") and note.get("text"):
            return str(note["text"])[:300]
    return None


STATIONS: tuple[dict[str, str], ...] = (
    {
        "id": "founder",
        "name": "Идеи",
        "sign": "💡",
        "does": "хочет: пишет, что делаем дальше",
    },
    {
        "id": "director",
        "name": "Контора",
        "sign": "🗂️",
        "does": "режет директиву на задачи",
    },
    {
        "id": "research",
        "name": "Разведка",
        "sign": "🔭",
        "does": "что продаётся, кому, за сколько",
    },
    {"id": "production", "name": "Цех", "sign": "⚙️", "does": "делает продукт файлом"},
    {"id": "critic", "name": "ОТК", "sign": "🔍", "does": "принимает или бракует"},
    {"id": "marketing", "name": "Витрина", "sign": "🏷️", "does": "цена и оффер"},
    {
        "id": "sales",
        "name": "Отгрузка",
        "sign": "📦",
        "does": "публикует, отвечает, отдаёт",
    },
    {"id": "cash", "name": "Касса", "sign": "💰", "does": "считает деньги"},
)

_PART_STATION = {"draft": "critic", "rework": "production", "ready": "marketing"}


def _part(product: dict[str, Any], st: journal.State) -> dict[str, Any]:
    """Where a product is on the belt now, as a part with its route card."""
    offers = [o for o in st.offers.values() if o["product"] == product["id"]]
    station = _PART_STATION.get(product["status"], "critic")
    note = {
        "draft": "ждёт ОТК",
        "rework": "брак: назад в цех",
        "ready": "принят ОТК",
    }.get(product["status"], "")
    if offers:
        offer = offers[-1]
        if offer["status"] == "published":
            station = "sales"
            note = f"на витрине за {offer['price']:.0f} ₽"
        else:
            station = "sales"
            note = f"оффер {offer['id']} ждёт публикации"
    sold = sum(
        float(s.get("amount") or 0)
        for s in st.money["sales"]
        if s.get("product") == product["id"]
    )
    shipped = sum(1 for d in st.deliveries if d.get("product") == product["id"])
    if sold:
        station = "cash"
        note = f"продано на {sold:.0f} ₽" + (
            f", отгружено {shipped}" if shipped else ""
        )
    return {
        "id": product["id"],
        "title": product["title"],
        "project": product["project"],
        "station": station,
        "status": product["status"],
        "note": note,
        "reviews": len(product["reviews"]),
        "path": product["path"],
        "sold": sold,
        "shipped": shipped,
    }


def board(st: journal.State | None = None) -> dict[str, Any]:
    st = st or journal.state()
    from corporation import cycle

    current = cycle.current()
    stations: list[dict[str, Any]] = []
    tasks = list(st.tasks.values())
    for spec in STATIONS:
        role = spec["id"]
        mine = [t for t in tasks if t["role"] == role]
        bin_items = [t for t in mine if t["status"] == "queued"]
        working = [t for t in mine if t["status"] == "working"]
        done = [t for t in mine if t["status"] == "done"]
        failed = [t for t in mine if t["status"] == "failed"]
        last = (
            max((t.get("closed") or t.get("opened") or "" for t in mine), default=None)
            or None
        )
        station = {
            **spec,
            "bin": [
                {
                    "id": t["id"],
                    "text": str(t["text"])[:120],
                    "project": t["project"],
                    "turns": t["turns"],
                }
                for t in bin_items[:8]
            ],
            "bin_count": len(bin_items),
            "working": [
                {
                    "id": t["id"],
                    "text": str(t["text"])[:120],
                    "project": t["project"],
                    "turns": t["turns"],
                }
                for t in working
            ],
            "done": len(done),
            "failed": len(failed),
            "last": last,
            "active": current.get("role") == role,
        }
        if role == "founder":
            station["bin_count"] = (
                0
                if any(not d.get("project") for d in st.directives)
                else station["bin_count"]
            )
            station["done"] = sum(1 for d in st.directives if d.get("by") == "founder")
            station["note"] = f"последняя смена {st.last_founder or '—'}"
        if role == "director":
            waiting = [d for d in st.directives if not d.get("project")]
            station["bin"] = [
                {"id": f"d{d['seq']}", "text": d["text"][:120], "by": d.get("by")}
                for d in waiting[:8]
            ]
            station["bin_count"] = len(waiting)
            station["done"] = sum(1 for p in st.projects.values())
        if role == "cash":
            money = cash.summary(st)
            station["done"] = money["sales"]
            station["note"] = (
                f"{money['confirmed']:.0f} ₽ подтверждено, {money['proposed']:.0f} ₽ предложено"
            )
            station["money"] = money
        stations.append(station)

    parts = [_part(p, st) for p in st.products.values()]
    warehouse = [p for p in parts if p["status"] == "ready" and not p["sold"]]
    shelf = [p for p in parts if p["station"] == "sales"]
    chute = [
        {
            "id": t["id"],
            "role": t["role"],
            "reason": str(t.get("result") or "")[:160],
            "at": t.get("closed"),
        }
        for t in tasks
        if t["status"] == "failed"
    ][-10:]
    from corporation.roles import ROLES

    open_tasks = sorted(
        [t for t in tasks if t["status"] in ("working", "queued")],
        key=lambda t: (t["status"] != "working", t.get("opened") or ""),
    )
    return {
        "company": {
            "running": bool(st.cycle.get("running")),
            "agents": len(ROLES),
            "revenue_today": _today(st)["revenue"],
            "growth_month_pct": _month_growth(st),
            "goal": st.goal,
            "since": st.cycle.get("since"),
        },
        "orchestrator": {
            "active": current.get("role") in ("founder", "director"),
            "report": _latest_report(st),
            "last_founder": st.last_founder,
            "directives_open": sum(1 for d in st.directives if not d.get("project")),
        },
        "departments": _departments(st, current),
        "today": _today(st),
        "timeline": _timeline(st),
        "metrics": {
            "avg_turn_seconds": _avg_turn_seconds(st),
            "steps": st.cycle.get("steps", 0),
            "records": st.records,
            "running_since": st.cycle.get("since"),
        },
        "people": {name: ROLES[name].person for name in ROLES},
        "current_tasks": [
            {
                "id": t["id"],
                "role": t["role"],
                "status": t["status"],
                "text": str(t["text"])[:90],
                "project": t["project"],
            }
            for t in open_tasks[:8]
        ],
        "whistle": bool(st.cycle.get("running")),
        "smoke": st.cycle.get("steps", 0),
        "shift": {
            "role": current.get("role"),
            "task": current.get("task"),
            "since": st.cycle.get("last_tick"),
        },
        "goal": st.goal,
        "stations": stations,
        "belt": parts[-20:],
        "warehouse": warehouse,
        "shelf": shelf,
        "shipped": len(st.deliveries),
        "chute": chute,
        "court": st.court[-10:],
        "clients_waiting": sum(
            1
            for c in st.clients.values()
            if c["messages"] and c["messages"][-1]["from"] == "client"
        ),
        "cash": cash.summary(st),
        "records": st.records,
    }
