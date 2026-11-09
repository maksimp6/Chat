"""The synchronous cycle: one step at a time, one role, one runner.

Owner, 08.11.2026: "we should think on how to utilize batching and different models,
so we must make system synchronous step after step". So:

- A **step** is one turn of one role on one task, through the app's own chat route
  (POST /api/chat on this server, as the Court's own sittings do): the turn is
  stored in the project's dialog, judged by the Court, embedded, and its tools run
  under the role's policy (``tool_policy`` names it; the policy comes from this
  module, not from the model).
- A **shift** is a run of steps of the same role, which is the same model and the
  same instructions: the scheduler keeps the current role while it has work, so the
  runner's context stays on one kind of job (batching), and changes the model only
  at a shift boundary. A model change is ``POST /v1/model`` on the runner, which
  rewrites the stick's cfg and reboots; the cycle waits for the runner to come back.
- Nothing runs in parallel. While the owner is talking in the chat the cycle waits
  (GET /api/chat/turns/running); the owner's turn is always first.
- Every step is a ledger record before and after; a crash leaves the task
  ``working`` and the next start puts it back in the queue.

The cycle runs only in the ``running`` state the owner's "корпорация: старт" put
in the ledger; that record is its authority, and a restart of the app resumes it.
"""

from __future__ import annotations

import calendar
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from corporation import ledger as journal
from corporation.roles import ROLE_ORDER, ROLES, WATCHDOG_ROUNDS, Role, policy_name

logger = logging.getLogger("logos.corporation.cycle")

__all__ = ["current", "ensure_started", "start", "status", "step_once", "stop"]

IDLE_SECONDS = 30  # when nothing is queued
BETWEEN_STEPS_SECONDS = 2
OWNER_WAIT_SECONDS = 5
SNAPSHOT_EVERY = 10
MODEL_SWITCH_TIMEOUT = 300
FOUNDER_EVERY = 0  # the founder takes the shift whenever the queue is empty (owner: no restrictions)

_current: dict[str, Any] = {}
_thread: threading.Thread | None = None
_stop = threading.Event()
_lock = threading.Lock()


def current() -> dict[str, Any]:
    """The step being run: role, task, project, product, offer (for the tools)."""
    return dict(_current)


# ----- talking to this server and to the runner ------------------------------------------------


def _port() -> str:
    return os.environ.get("LOGOS_PORT") or "8080"


def _local(
    method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 1800
) -> Any:
    req = urllib.request.Request(
        f"http://127.0.0.1:{_port()}{path}",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raise ValueError(
            f"{path}: HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:500]}"
        ) from exc


def _owner_busy() -> bool:
    try:
        return (
            int(
                (_local("GET", "/api/chat/turns/running", timeout=10) or {}).get(
                    "running"
                )
                or 0
            )
            > 0
        )
    except Exception:
        return False


def _runner_url() -> str:
    from ollama_client import settings

    return settings.url()


def _runner_status() -> dict[str, Any]:
    import requests

    response = requests.get(_runner_url().rstrip("/") + "/v1/status", timeout=20)
    response.raise_for_status()
    return response.json()


def _runner_model() -> str | None:
    try:
        return str(_runner_status().get("model") or "") or None
    except Exception:
        return None


def _switch_model(role: Role) -> bool:
    """Make the runner boot the role's model file; True when it answers with it."""
    import requests

    from ollama_client import settings

    loaded = _runner_model()
    if loaded is None or loaded == role.gguf or role.gguf in loaded:
        return True
    book = journal.ledger()
    book.append(
        "model.switch", "cycle", {"from": loaded, "to": role.gguf, "role": role.name}
    )
    try:
        requests.post(
            _runner_url().rstrip("/") + "/v1/model",
            json={"name": role.gguf},
            timeout=30,
        )
    except Exception as exc:  # the runner reboots right after it answers; a broken connection is normal
        logger.info("[CORP] model switch request ended with %s", exc)
    settings.forget()
    deadline = time.time() + MODEL_SWITCH_TIMEOUT
    while time.time() < deadline and not _stop.is_set():
        time.sleep(10)
        loaded = _runner_model()
        if loaded and (loaded == role.gguf or role.gguf in loaded):
            book.append("note", "cycle", {"text": f"the runner is back with {loaded}"})
            return True
    book.append(
        "cycle.error",
        "cycle",
        {
            "error": f"the runner did not come back with {role.gguf} in {MODEL_SWITCH_TIMEOUT} s"
        },
    )
    return False


# ----- the scheduler ----------------------------------------------------------------------------


def _handoffs(st: journal.State) -> None:
    """The pipeline's own tasks, opened by code, not by the director: a drafted product
    goes to the critic, a reworked one back to production, a ready one to marketing, a
    drafted offer to sales, a waiting buyer to sales."""
    book = journal.ledger()
    opened = {
        (t["role"], t.get("product"), t.get("offer"), t.get("client"))
        for t in st.tasks.values()
        if t["status"] in ("queued", "working")
    }
    for product in st.products.values():
        project = product["project"]
        if st.projects.get(project, {}).get("status") != "open":
            continue
        reviews = product["reviews"]
        if (
            product["status"] == "draft"
            and ("critic", product["id"], None, None) not in opened
        ):
            tid = st.next_id("t", st.tasks)
            st.tasks[tid] = {
                "status": "queued",
                "role": "critic",
                "product": product["id"],
                "offer": None,
                "client": None,
            }
            book.append(
                "task.open",
                "cycle",
                {
                    "id": tid,
                    "role": "critic",
                    "product": product["id"],
                    "text": f"Прочитай продукт {product['id']} «{product['title']}» в файле {product['path']} и вынеси вердикт "
                    f"review_product: ready или rework с конкретными правками. Описание продукта: {product['summary']}",
                },
                project=project,
                task=tid,
            )
        elif (
            product["status"] == "rework"
            and ("production", product["id"], None, None) not in opened
        ):
            tid = st.next_id("t", st.tasks)
            st.tasks[tid] = {
                "status": "queued",
                "role": "production",
                "product": product["id"],
                "offer": None,
                "client": None,
            }
            notes = reviews[-1].get("notes", "") if reviews else ""
            book.append(
                "task.open",
                "cycle",
                {
                    "id": tid,
                    "role": "production",
                    "product": product["id"],
                    "text": f"Доработай продукт {product['id']} «{product['title']}» в файле {product['path']} по замечаниям критика и "
                    f"перезапиши файл; затем снова draft_product с тем же путём не нужен — только finish_task. Замечания: {notes}",
                },
                project=project,
                task=tid,
            )
        elif (
            product["status"] == "ready"
            and not any(o["product"] == product["id"] for o in st.offers.values())
            and ("marketing", product["id"], None, None) not in opened
        ):
            tid = st.next_id("t", st.tasks)
            st.tasks[tid] = {
                "status": "queued",
                "role": "marketing",
                "product": product["id"],
                "offer": None,
                "client": None,
            }
            book.append(
                "task.open",
                "cycle",
                {
                    "id": tid,
                    "role": "marketing",
                    "product": product["id"],
                    "text": f"Составь оффер (draft_offer) для готового продукта {product['id']} «{product['title']}», файл {product['path']}. "
                    f"Описание: {product['summary']}",
                },
                project=project,
                task=tid,
            )
    for offer in st.offers.values():
        if (
            offer["status"] == "draft"
            and st.channels.get(offer["channel"], {}).get("enabled")
            and ("sales", None, offer["id"], None) not in opened
        ):
            tid = st.next_id("t", st.tasks)
            st.tasks[tid] = {
                "status": "queued",
                "role": "sales",
                "product": offer["product"],
                "offer": offer["id"],
                "client": None,
            }
            book.append(
                "task.open",
                "cycle",
                {
                    "id": tid,
                    "role": "sales",
                    "offer": offer["id"],
                    "product": offer["product"],
                    "text": f"Опубликуй оффер {offer['id']} в канал {offer['channel']} (channels publish), затем finish_task.",
                },
                project=offer["project"],
                task=tid,
            )
    for client in st.clients.values():
        messages = client["messages"]
        if (
            messages
            and messages[-1]["from"] == "client"
            and ("sales", None, None, client["id"]) not in opened
        ):
            if not st.channels.get(client.get("channel") or "telegram", {}).get(
                "enabled"
            ):
                continue
            tid = st.next_id("t", st.tasks)
            st.tasks[tid] = {
                "status": "queued",
                "role": "sales",
                "product": None,
                "offer": None,
                "client": client["id"],
            }
            project = next(
                (p["id"] for p in st.projects.values() if p["status"] == "open"), None
            )
            book.append(
                "task.open",
                "cycle",
                {
                    "id": tid,
                    "role": "sales",
                    "client": client["id"],
                    "text": f"Покупатель {client['id']} ({client.get('name') or 'без имени'}) написал: «{messages[-1]['text']}». "
                    f"Прочитай inbox, ответь (reply); если хочет купить — выставь счёт (invoice) по офферу; если оплатил — deliver.",
                },
                project=project,
                task=tid,
            )


def _pick(st: journal.State) -> tuple[str, dict[str, Any] | None] | None:
    """(role, task) of the next step, or None when there is nothing to do."""
    queued = [
        t
        for t in st.tasks.values()
        if t["status"] == "queued"
        and st.projects.get(t["project"], {}).get("status", "open") == "open"
    ]
    last = st.cycle.get("last_role")
    if last and last != "director":
        same = [t for t in queued if t["role"] == last]
        if same:
            return last, same[0]
    for role in ROLE_ORDER:
        if role == "director":
            continue
        mine = [t for t in queued if t["role"] == role]
        if mine:
            return role, mine[0]
    if any(not d.get("project") for d in st.directives):
        return "director", None
    # Nothing queued and no directive waiting: the one who wants gets the shift (owner,
    # 08.11.2026: "who is going to want and move forward"). Not more often than every
    # FOUNDER_EVERY seconds (0: as soon as the queue is empty).
    if _seconds_since(st.last_founder) >= FOUNDER_EVERY:
        return "founder", None
    return None


def _seconds_since(stamp: str | None) -> float:
    if not stamp:
        return float("inf")
    try:
        return time.time() - calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return float("inf")


def _project_conversation(st: journal.State, project_id: str | None) -> str:
    """The dialog of a project (or the company's own), created on first use."""
    title = (
        "🏢 Компания"
        if not project_id
        else f"🏢 {project_id} {st.projects[project_id]['title'][:60]}"
    )
    for entry in reversed(journal.ledger().entries()):
        data = entry.get("data") or {}
        if (
            entry["kind"] == "note"
            and data.get("conversation")
            and data.get("for") == (project_id or "company")
        ):
            return str(data["conversation"])
    from ollama_client import settings

    made = _local(
        "POST",
        "/api/conversations",
        {"model": next(iter(settings.MODELS)), "title": title},
        timeout=60,
    )
    journal.ledger().append(
        "note",
        "cycle",
        {
            "text": f"dialog for {project_id or 'the company'}",
            "conversation": made["id"],
            "for": project_id or "company",
        },
        project=project_id,
    )
    return str(made["id"])


def _brief(st: journal.State, role: Role, task: dict[str, Any] | None) -> str:
    from corporation.lines import menu, recipe

    lines = [f"Роль: {role.title}.", st.brief()]
    if task is not None:
        lines.append(
            f"Поручение {task['id']} (проект {task['project']}, ход {task['turns'] + 1}{' из ' + str(role.turns) if role.turns else ''}):\n{task['text']}"
        )
        project = st.projects.get(task.get("project") or "")
        if project and role.name in (
            "research",
            "production",
            "critic",
            "marketing",
            "sales",
        ):
            lines.append(recipe(project.get("line")))
        folder = journal.products_dir() / (task.get("project") or "x")
        if role.writes_products:
            lines.append(f"Каталог продуктов проекта: {folder}")
        if task.get("product") and task["product"] in st.products:
            product = st.products[task["product"]]
            lines.append(
                f"Продукт {product['id']}: {product['title']} — {product['path']} (статус {product['status']})."
            )
        if task.get("offer") and task["offer"] in st.offers:
            offer = st.offers[task["offer"]]
            lines.append(
                f"Оффер {offer['id']}: {offer['price']:.0f} {offer['currency']}, канал {offer['channel']}."
            )
        if task.get("last_reply"):
            lines.append(
                f"Твой прошлый ход по этому поручению закончился словами: {task['last_reply']}"
            )
        lines.append(
            "Закончи поручение в этом ходе: сделай работу инструментами и вызови corporation finish_task или fail_task."
        )
    elif role.name == "founder":
        from corporation.roles import DEFAULT_GOAL

        lines.append(f"Постоянная цель компании: {st.goal or DEFAULT_GOAL}")
        history = []
        for project in list(st.projects.values())[-6:]:
            sold = sum(
                float(s.get("amount") or 0)
                for s in st.money["sales"]
                if s.get("product")
                in {
                    p["id"]
                    for p in st.products.values()
                    if p["project"] == project["id"]
                }
            )
            history.append(
                f"{project['id']} «{project['title']}» ({project['status']}): продано на {sold:.0f} RUB"
            )
        failed = [t for t in st.tasks.values() if t["status"] == "failed"][-5:]
        lines.append(
            "Прошлые проекты: "
            + ("; ".join(history) if history else "ещё не было ни одного.")
        )
        if failed:
            lines.append(
                "Провалы: "
                + "; ".join(
                    f"{t['id']} [{t['role']}] {str(t.get('result') or '')[:120]}"
                    for t in failed
                )
            )
        if st.directives:
            last = st.directives[-1]
            lines.append(
                f"Последняя директива ([{last['seq']}], {last.get('by')}): {last['text']}"
            )
        lines.append(
            "Линии производства компании (назови одну в директиве):\n" + menu()
        )
        lines.append(
            "Очередь пуста. Реши, что компания делает дальше, и запиши директиву (corporation directive)."
        )
    else:
        pending = [d for d in st.directives if not d.get("project")]
        lines.append(
            "Директивы без проекта:\n"
            + "\n".join(
                f"[{d['seq']}] ({d.get('by')}) {d['text']}" for d in pending[:3]
            )
        )
        lines.append("Линии (поле line в open_project):\n" + menu())
        lines.append(
            "Открой проект по первой директиве на подходящей линии и поставь задачи ролям; затем кратко отчитайся (note)."
        )
    return "\n\n".join(lines)


def step_once() -> dict[str, Any]:
    """One step of the cycle. Returns what happened (for the status and the log)."""
    global _current
    st = journal.state()
    if not _current:
        # a task left "working" by a crash of the app mid-step goes back to the queue (its turns counted)
        for task in list(st.tasks.values()):
            if task["status"] == "working":
                journal.ledger().append(
                    "task.open",
                    "cycle",
                    {
                        "id": task["id"],
                        "role": task["role"],
                        "text": task["text"],
                        "product": task.get("product"),
                        "offer": task.get("offer"),
                        "client": task.get("client"),
                        "reopened": True,
                    },
                    project=task["project"],
                    task=task["id"],
                )
        st = journal.state()
    _handoffs(st)
    st = journal.state()
    try:
        from corporation import channels

        if channels.poll_inbox(st):
            st = journal.state()
            _handoffs(st)
            st = journal.state()
    except Exception as exc:
        journal.ledger().append(
            "cycle.error", "cycle", {"error": f"inbox: {type(exc).__name__}: {exc}"}
        )
    picked = _pick(st)
    if picked is None:
        return {"idle": True}
    role_name, task = picked
    role = ROLES[role_name]
    if not _switch_model(role):
        return {"idle": True, "error": "model switch failed"}
    book = journal.ledger()
    _current = {
        "role": role_name,
        "task": task["id"] if task else None,
        "project": task["project"] if task else None,
        "product": task.get("product") if task else None,
        "offer": task.get("offer") if task else None,
    }
    conversation = _project_conversation(st, task["project"] if task else None)
    if task is not None:
        book.append(
            "task.start",
            role_name,
            {"turn": task["turns"] + 1},
            project=task["project"],
            task=task["id"],
        )
    started = time.time()
    try:
        answer = _local(
            "POST",
            "/api/chat",
            {
                "conversation_id": conversation,
                "message": _brief(st, role, task),
                "source": f"corporation:{role_name}",
                "tool_policy": policy_name(role_name),
                "params": {
                    "instructions": role.prompt(),
                    "max_rounds": role.rounds or WATCHDOG_ROUNDS,
                },
            },
            timeout=3600,
        )
        reply = str(answer.get("reply") or "")
        usage = answer.get("usage") or {}
        error = reply.startswith("⚠️")
    except Exception as exc:
        reply, usage, error = f"{type(exc).__name__}: {exc}", {}, True
    seconds = round(time.time() - started, 1)
    book.append(
        "task.turn",
        role_name,
        {
            "reply": reply,
            "usage": usage,
            "seconds": seconds,
            "error": error,
            "conversation": conversation,
        },
        project=task["project"] if task else None,
        task=task["id"] if task else None,
    )
    st = journal.state()
    if task is not None:
        fresh = st.tasks.get(task["id"], task)
        if fresh["status"] == "working":
            if error:
                book.append(
                    "task.failed",
                    "cycle",
                    {"reason": f"the turn failed: {reply[:500]}"},
                    project=task["project"],
                    task=task["id"],
                )
                book.append(
                    "court.case",
                    "court",
                    {"task": task["id"], "role": role_name, "reason": reply[:1000]},
                    project=task["project"],
                    task=task["id"],
                )
            elif role.turns and fresh["turns"] >= role.turns:
                book.append(
                    "task.failed",
                    "cycle",
                    {"reason": f"{role.turns} turns without finish_task"},
                    project=task["project"],
                    task=task["id"],
                )
            else:
                # another turn later: the task goes back to the queue with its turns counted
                book.append(
                    "task.open",
                    "cycle",
                    {
                        "id": task["id"],
                        "role": role_name,
                        "text": task["text"],
                        "product": task.get("product"),
                        "offer": task.get("offer"),
                        "client": task.get("client"),
                        "reopened": True,
                    },
                    project=task["project"],
                    task=task["id"],
                )
    book.append(
        "cycle.tick",
        "cycle",
        {
            "role": role_name,
            "task": task["id"] if task else None,
            "seconds": seconds,
            "error": error,
        },
    )
    _current = {}
    if st.cycle["steps"] % SNAPSHOT_EVERY == SNAPSHOT_EVERY - 1:
        made = book.snapshot()
        book.append("backup", "cycle", made)
    return {
        "role": role_name,
        "task": task["id"] if task else None,
        "seconds": seconds,
        "error": error,
    }


# ----- the thread -------------------------------------------------------------------------------


def _run() -> None:
    logger.info("[CORP] the cycle runs")
    while not _stop.is_set():
        try:
            if not journal.state().cycle.get("running"):
                break
            if _owner_busy():
                _stop.wait(OWNER_WAIT_SECONDS)
                continue
            result = step_once()
            _stop.wait(IDLE_SECONDS if result.get("idle") else BETWEEN_STEPS_SECONDS)
        except Exception as exc:
            logger.exception("[CORP] step failed")
            try:
                journal.ledger().append(
                    "cycle.error", "cycle", {"error": f"{type(exc).__name__}: {exc}"}
                )
            except Exception:
                pass
            _stop.wait(IDLE_SECONDS)
    logger.info("[CORP] the cycle stopped")


def _spawn() -> None:
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_run, name="logos-corporation", daemon=True)
        _thread.start()


def start(command: str) -> dict[str, Any]:
    """The owner's start: recorded as the cycle's authority, then the thread."""
    book = journal.ledger()
    check = book.verify()
    if not check.get("ok"):
        book.append(
            "court.case",
            "court",
            {"reason": f"ledger chain broken at {check.get('broken_at')}"},
        )
        raise ValueError(
            f"the ledger chain is broken at record {check.get('broken_at')}: the cycle does not start"
        )
    book.append("cycle.start", "owner", {"command": command})
    _spawn()
    return status()


def stop(reason: str = "owner") -> dict[str, Any]:
    journal.ledger().append("cycle.stop", "owner", {"reason": reason})
    _stop.set()
    return status()


def ensure_started() -> None:
    """At boot: the cycle resumes when the ledger says it was running (and re-embeds what the
    vector database missed while the runner was away)."""
    try:
        st = journal.state()
    except Exception:
        logger.exception("[CORP] the ledger could not be read at boot")
        return
    try:
        journal.ledger().reembed_missing()
    except Exception:
        logger.exception("[CORP] re-embedding failed")
    if st.cycle.get("running"):
        _spawn()


def status() -> dict[str, Any]:
    st = journal.state()
    from corporation import cash, channels

    return {
        "summary": st.summary(),
        "cash": cash.summary(st),
        "thread_alive": bool(_thread is not None and _thread.is_alive()),
        "current": current(),
        "channels_configured": channels.configured(),
        "runner_model": _runner_model(),
    }
