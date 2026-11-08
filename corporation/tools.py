"""The ``corporation`` tool: the roles' hands on the company's own books.

One function (the category) with ``tool`` picking the action, as every Logos tool.
Every action writes the ledger; the actor is the role of the step the cycle is
running (``cycle.current()``), never a name the model supplies.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from corporation import ledger as journal
from corporation.roles import ROLES

__all__ = ["CORPORATION_TOOLS"]

_ID = {"type": "string", "minLength": 1}
_TEXT = {"type": "string", "minLength": 1}


def _actor() -> str:
    from corporation import context, cycle

    return context.current() or cycle.current().get("role") or "owner"


def _step() -> dict[str, Any]:
    from corporation import cycle

    return cycle.current()


def _safe_segment(value: str) -> str:
    text = re.sub(r"[^\w\-]+", "-", str(value or ""), flags=re.UNICODE).strip("-")
    return text[:60] or "x"


def _project_dir(project_id: str) -> Path:
    return journal.products_dir() / _safe_segment(project_id)


def _open_project(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    st = journal.state()
    pid = st.next_id("p", st.projects)
    directive = args.get("directive")
    directive_seq = int(directive) if isinstance(directive, (int, float)) or (isinstance(directive, str) and directive.isdigit()) else None
    if directive_seq is None:
        pending = [d for d in st.directives if not d.get("project")]
        directive_seq = pending[0]["seq"] if pending else None
    from corporation.lines import DEFAULT_LINE, LINES

    # a known line brings its route card; any other word names a new line of the company's own
    line = str(args.get("line") or DEFAULT_LINE).lower()
    known = line in LINES
    folder = _project_dir(pid)
    folder.mkdir(parents=True, exist_ok=True)
    entry = journal.ledger().append(
        "project.open", _actor(),
        {"id": pid, "title": str(args["title"]), "goal": str(args.get("goal") or ""),
         "directive": directive_seq, "folder": str(folder), "line": line},
        project=pid,
    )
    return {"success": True, "project": pid, "line": line, "known_line": known, "folder": str(folder), "seq": entry["seq"]}


def _open_task(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    st = journal.state()
    project = str(args["project"])
    if project not in st.projects:
        raise ValueError(f"no such project: {project}; projects: {', '.join(st.projects) or 'none'}")
    role = str(args["role"])
    if role not in ROLES:
        raise ValueError(f"role must be one of: {', '.join(ROLES)}")
    tid = st.next_id("t", st.tasks)
    text = str(args["text"])
    data = {"id": tid, "role": role, "text": text, "folder": str(_project_dir(project))}
    for key in ("product", "offer", "client"):
        if args.get(key):
            data[key] = str(args[key])
    entry = journal.ledger().append("task.open", _actor(), data, project=project, task=tid)
    return {"success": True, "task": tid, "role": role, "seq": entry["seq"]}


def _current_task(args: dict[str, Any]) -> str:
    task = str(args.get("task") or _step().get("task") or "")
    if not task:
        raise ValueError("task is required")
    return task


def _finish_task(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    st = journal.state()
    tid = _current_task(args)
    task = st.tasks.get(tid)
    if task is None:
        raise ValueError(f"no such task: {tid}")
    if task["status"] in ("done", "failed"):
        return {"success": True, "task": tid, "status": task["status"], "note": "already closed"}
    entry = journal.ledger().append("task.done", _actor(), {"result": str(args.get("result") or "")},
                                    project=task["project"], task=tid)
    return {"success": True, "task": tid, "status": "done", "seq": entry["seq"]}


def _fail_task(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    st = journal.state()
    tid = _current_task(args)
    task = st.tasks.get(tid)
    if task is None:
        raise ValueError(f"no such task: {tid}")
    entry = journal.ledger().append("task.failed", _actor(), {"reason": str(args.get("reason") or "")},
                                    project=task["project"], task=tid)
    return {"success": True, "task": tid, "status": "failed", "seq": entry["seq"]}


def _draft_product(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    st = journal.state()
    project = str(args.get("project") or _step().get("project") or "")
    if project not in st.projects:
        raise ValueError(f"no such project: {project}")
    path = Path(str(args["path"])).expanduser()
    if not path.is_absolute():
        path = _project_dir(project) / path
    try:
        path.resolve().relative_to(journal.products_dir().resolve())
    except ValueError as exc:
        raise ValueError(f"a product must be a file under {journal.products_dir()}") from exc
    if not path.is_file():
        raise ValueError(f"the product file does not exist yet: {path}; write it first (filesystem write_file)")
    size = path.stat().st_size
    prid = st.next_id("pr", st.products)
    entry = journal.ledger().append(
        "product.draft", _actor(),
        {"id": prid, "title": str(args["title"]), "path": str(path), "bytes": size,
         "summary": str(args.get("summary") or "")},
        project=project, task=_step().get("task"), product=prid,
    )
    return {"success": True, "product": prid, "path": str(path), "bytes": size, "seq": entry["seq"]}


def _review_product(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    st = journal.state()
    prid = str(args.get("product") or _step().get("product") or "")
    product = st.products.get(prid)
    if product is None:
        raise ValueError(f"no such product: {prid}")
    verdict = str(args.get("verdict") or "").lower()
    if verdict not in ("ready", "rework"):
        raise ValueError("verdict must be ready or rework")
    entry = journal.ledger().append(
        "product.review", _actor(),
        {"verdict": verdict, "notes": str(args.get("notes") or "")},
        project=product["project"], task=_step().get("task"), product=prid,
    )
    return {"success": True, "product": prid, "status": verdict, "seq": entry["seq"]}


def _draft_offer(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    st = journal.state()
    prid = str(args.get("product") or _step().get("product") or "")
    product = st.products.get(prid)
    if product is None:
        raise ValueError(f"no such product: {prid}")
    if product["status"] != "ready":
        raise ValueError(f"product {prid} is {product['status']}, not ready: the critic has not passed it")
    try:
        price = float(args["price"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("price must be a number (RUB)") from exc
    channel = str(args.get("channel") or "telegram").lower()
    oid = st.next_id("o", st.offers)
    entry = journal.ledger().append(
        "offer.draft", _actor(),
        {"id": oid, "price": round(price), "currency": "RUB", "channel": channel, "text": str(args["text"])},
        project=product["project"], task=_step().get("task"), product=prid, offer=oid,
    )
    return {"success": True, "offer": oid, "price": round(price), "channel": channel, "seq": entry["seq"]}


def _directive(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    """The founder's will: a directive the company gives itself (actor founder, never owner)."""
    text = str(args["text"])
    entry = journal.ledger().append("board.directive", _actor(), {"text": text, "why": str(args.get("why") or ""),
                                                                  "line": str(args.get("line") or "")})
    return {"success": True, "seq": entry["seq"], "note": "the director takes it on the next step"}


def _note(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    step = _step()
    entry = journal.ledger().append("note", _actor(), {"text": str(args["text"])},
                                    project=step.get("project"), task=step.get("task"))
    return {"success": True, "seq": entry["seq"]}


def _status(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    st = journal.state()
    step = _step()
    result: dict[str, Any] = {"success": True, "summary": st.summary(), "brief": st.brief()}
    if step.get("project") in st.projects:
        project = st.projects[step["project"]]
        result["project"] = {**project, "tasks": [st.tasks[t] for t in project["tasks"] if t in st.tasks][-12:]}
        result["products"] = [p for p in st.products.values() if p["project"] == step["project"]][-8:]
    else:
        result["projects"] = [p for p in st.projects.values() if p["status"] == "open"][-8:]
        result["directives_open"] = [d for d in st.directives if not d.get("project")][:5]
    if step.get("task") in st.tasks:
        result["task"] = st.tasks[step["task"]]
    return result


def _tool(fn, *, read_only: bool, schema: dict[str, Any], title: str, description: str) -> dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "parameters": {"type": "object", "properties": schema.get("properties", {}),
                       "required": schema.get("required", []), "additionalProperties": False},
        "capabilities": ["corporation"],
        "risk_level": "low" if read_only else "medium",
        "read_only": read_only,
        "requires_approval": False,
        "supported_transports": ["responses_api", "local_agent"],
        "executor": {"type": "local"},
        "func": fn,
    }


CORPORATION_TOOLS = {
    "open_project": _tool(
        _open_project, read_only=False,
        schema={"properties": {"title": _TEXT, "goal": _TEXT, "directive": {"type": ["integer", "string", "null"]},
                               "line": {"type": "string"}},
                "required": ["title", "goal"]},
        title="Открыть проект",
        description="Директор: новый проект по директиве (directive — номер записи; без него первая открытая) на одной из линий: android (исходники приложений), bots, texts, docs, data, translation. Создаёт каталог продуктов проекта.",
    ),
    "open_task": _tool(
        _open_task, read_only=False,
        schema={"properties": {"project": _ID, "role": {"type": "string", "enum": ["founder", "director", "research", "production", "critic", "marketing", "sales"]},
                               "text": _TEXT, "product": _ID, "offer": _ID, "client": _ID}, "required": ["project", "role", "text"]},
        title="Поставить задачу",
        description="Директор: задача роли в проекте; text — одно конкретное поручение с измеримым результатом.",
    ),
    "finish_task": _tool(
        _finish_task, read_only=False,
        schema={"properties": {"task": _ID, "result": _TEXT}, "required": ["result"]},
        title="Завершить задачу",
        description="Любая роль: текущее поручение выполнено; result — что сделано, где лежит.",
    ),
    "fail_task": _tool(
        _fail_task, read_only=False,
        schema={"properties": {"task": _ID, "reason": _TEXT}, "required": ["reason"]},
        title="Провалить задачу",
        description="Любая роль: поручение невыполнимо; reason — почему, чего не хватает.",
    ),
    "draft_product": _tool(
        _draft_product, read_only=False,
        schema={"properties": {"project": _ID, "title": _TEXT, "path": _TEXT, "summary": _TEXT}, "required": ["title", "path"]},
        title="Заявить продукт",
        description="Производство: записанный файл продукта (path — в каталоге продуктов проекта) становится черновиком продукта для критика.",
    ),
    "review_product": _tool(
        _review_product, read_only=False,
        schema={"properties": {"product": _ID, "verdict": {"type": "string", "enum": ["ready", "rework"]}, "notes": _TEXT}, "required": ["verdict", "notes"]},
        title="Рецензия продукта",
        description="Критик: ready — можно продавать; rework — список правок для производства.",
    ),
    "draft_offer": _tool(
        _draft_offer, read_only=False,
        schema={"properties": {"product": _ID, "price": {"type": "number"}, "channel": {"type": "string"}, "text": _TEXT},
                "required": ["price", "text"]},
        title="Оффер",
        description="Маркетинг: цена в рублях, канал (telegram, vk, email) и текст объявления для готового продукта.",
    ),
    "directive": _tool(
        _directive, read_only=False,
        schema={"properties": {"text": _TEXT, "why": _TEXT, "line": {"type": "string"}}, "required": ["text"]},
        title="Директива основателя",
        description="Основатель: что компания делает дальше (что, для кого, цена, почему принесёт деньги). Директор откроет по ней проект.",
    ),
    "note": _tool(
        _note, read_only=False,
        schema={"properties": {"text": _TEXT}, "required": ["text"]},
        title="Запись в журнал",
        description="Любая роль: заметка, отчёт правлению, факт.",
    ),
    "status": _tool(
        _status, read_only=True,
        schema={"properties": {}, "required": []},
        title="Положение дел",
        description="Касса, директивы, проекты, задачи, продукты — из журнала компании.",
    ),
}
