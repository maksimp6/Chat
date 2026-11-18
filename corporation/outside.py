"""The door for workers outside the box: a shift done by a strong model, in a role.

Owner, 18.11.2026: "we can cheat to have unlimited powerful smart workers with data so we
must use this advantage to for our company". The local runner is one small model; the
owner also has strong models with the internet at hand. So a role's shift may be done
outside the box: the outside worker does the thinking and the writing where it runs, and
brings the result here — product files and the same ``corporation`` / ``channels`` tool
calls a local turn would make. Nothing is generated in Logos for such a shift: no model
is called here, the local cycle is untouched; ``next`` and ``files`` hand the worker the
assignment and the project's files, never a buyer's text or contact.

What the door guarantees:

- the **role** is real (built-in or hired), and every record of the shift has it as actor;
- the **brain** is named by the caller and stamped into every record the shift writes
  (``data.via``, by the ledger itself), so the journal never passes outside work off as
  the local runner's, and the embeddings carry the same mark;
- **files** land only inside the project's own products folder;
- the **tools** are the company's own functions, with their own checks — a product must
  exist as a file, an offer needs a product the critic passed, and everything going
  outside still passes the Court gate in ``channels``;
- the shift ends with one ``note`` of the role: what was done, which calls, which files;
- the **next step** is planned here, by the cycle's own scheduling (the handoffs, the
  pick, the brief): the worker only executes, and no text a buyer wrote is in the answer;
- a **step** is taken once: a shift that names its step is refused, before it writes
  anything, when the step is no longer open.

Three functions: ``run_shift`` (action ``shift``), ``next_step`` (action ``next``) and
``project_files`` (action ``files``); ``/api/corporation`` is their only caller.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import stat
import threading
from pathlib import Path
from typing import Any

from corporation import context
from corporation import ledger as journal
from corporation.roles import all_roles, get_role, hired_roles

__all__ = ["next_step", "project_files", "run_shift"]

# One planner or one shift at a time: ``next`` and shifts do not allocate task ids from
# the same snapshot, and two callers of ``next`` open each pipeline task once.
_STEP_LOCK = threading.RLock()

_SITE = "site"  # the shop's channel; State.orders holds its orders only


def _check_file(project: str, item: Any) -> tuple[Path, bytes]:
    """One product file of the shift, checked: (its place inside
    ``products/<project>/``, its bytes). Nothing is written here."""
    if not isinstance(item, dict):
        raise ValueError("a file is {path, content} or {path, content_base64}")
    relative = Path(str(item.get("path") or ""))
    content = item.get("content")
    encoded = item.get("content_base64")
    # text as it is; anything else (an icon, an archive) as base64
    if isinstance(encoded, str) and content is None:
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"content_base64 of {relative} is not base64") from exc
    elif isinstance(content, str) and encoded is None:
        payload = content.encode("utf-8")
    else:
        raise ValueError("a file needs either text content or content_base64")
    if not str(relative) or str(relative) == ".":
        raise ValueError("a file needs a relative path")
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"a file path stays inside the project's folder: {relative}")
    from corporation.tools import _project_dir

    folder = _project_dir(project)
    target = folder / relative
    try:
        target.resolve().relative_to(folder.resolve())
    except ValueError as exc:
        raise ValueError(
            f"a file path stays inside the project's folder: {relative}"
        ) from exc
    return target, payload


_BOT_PATH = re.compile(r"/bot[^/\s]+/")
_ACCESS_TOKEN = re.compile(r"access_token=[^&\s]+")


def _redact(text: str) -> str:
    """An error text as it is returned and recorded: a bot path, an access token and
    whatever the gate's own expressions match are replaced by ``<redacted>``."""
    from corporation.channels import _SECRET_PATTERNS

    text = _BOT_PATH.sub("/bot<redacted>/", text)
    text = _ACCESS_TOKEN.sub("access_token=<redacted>", text)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("<redacted>", text)
    return text


def _call(call: Any) -> dict[str, Any]:
    """One tool call of the shift: the company's own function, with its own checks."""
    if not isinstance(call, dict):
        raise ValueError("a call is {category, tool, arguments}")
    category = str(call.get("category") or "corporation")
    name = str(call.get("tool") or "")
    arguments = call.get("arguments")
    if category == "corporation":
        from corporation.tools import CORPORATION_TOOLS as table
    elif category == "channels":
        from corporation.channels import CHANNEL_TOOLS as table
    else:
        raise ValueError("category is corporation or channels")
    if name not in table:
        raise ValueError(f"no such {category} action: {name}; actions: {', '.join(table)}")
    try:
        result = table[name]["func"](dict(arguments or {}))
    except Exception as exc:  # the tool's own refusal is the answer, as in a local turn
        return {
            "category": category,
            "tool": name,
            "success": False,
            "error": _redact(f"{type(exc).__name__}: {exc}"),
        }
    return {"category": category, "tool": name, "success": True, "result": result}


# ----- the next step ------------------------------------------------------------------------


def _label(cid: Any, orders: dict[str, Any]) -> str:
    """A client as the answer names it: a site order by its own id (the shop makes
    it), a client of any other channel by a digest of its id."""
    if not cid:
        return ""
    cid = str(cid)
    if cid in orders:
        return cid
    return "c-" + hashlib.sha256(cid.encode("utf-8")).hexdigest()[:12]


def _key(task: dict[str, Any], orders: dict[str, Any]) -> str:
    """What a task is about; the same for a task the handoffs open again under a new
    id."""
    return "|".join(
        [str(task.get(k) or "") for k in ("role", "project", "product", "offer")]
        + [_label(task.get("client"), orders)]
    )


def _id(value: Any) -> Any:
    """The id of a record given whole or by its id."""
    return value.get("id") if isinstance(value, dict) else value


def _inside(path: Any, folder: Path) -> str | None:
    """A stored path as a worker names it: relative to the project's folder, POSIX;
    None when it is not inside that folder."""
    if not path:
        return ""
    try:
        return Path(str(path)).resolve().relative_to(folder.resolve()).as_posix()
    except (OSError, RuntimeError, ValueError):
        return None


def _misplaced(product: dict[str, Any]) -> bool:
    """Whether the product is registered outside its project's folder."""
    from corporation.tools import _project_dir

    folder = _project_dir(product.get("project") or "")
    return _inside(product.get("path"), folder) is None


def _unpaid(st: journal.State) -> list[dict[str, Any]]:
    """The site orders whose product the owner has not confirmed as paid."""
    return [
        order
        for cid, order in st.orders.items()
        if order["product"] not in st.clients.get(cid, {}).get("paid", [])
    ]


def _project_view(st: journal.State, pid: str | None) -> dict[str, Any] | None:
    """A project for the worker: what it is, its tasks and its products."""
    from corporation.tools import _project_dir

    project = st.projects.get(pid or "")
    if project is None:
        return None
    folder = _project_dir(project["id"])
    return {
        "id": project["id"],
        "title": project["title"],
        "goal": project["goal"],
        "line": project.get("line"),
        "dir": folder.name,
        "tasks": [
            {
                "id": tid,
                "role": st.tasks[tid]["role"],
                "status": st.tasks[tid]["status"],
                "product": st.tasks[tid].get("product"),
            }
            for tid in project["tasks"]
            if tid in st.tasks
        ],
        "products": [
            {
                "id": product["id"],
                "path": _inside(product["path"], folder),
                "status": product["status"],
            }
            for product in st.products.values()
            if product["project"] == project["id"]
        ],
    }


def _product_view(st: journal.State, prid: str | None) -> dict[str, Any] | None:
    """A product for the worker: where it lies, what the critic said, how the shop
    reads it."""
    from corporation import shop
    from corporation.tools import _project_dir

    product = st.products.get(prid or "")
    if product is None:
        return None
    folder = _project_dir(product["project"] or "")
    showcase, _problems = shop._showcase(product)
    # the descriptor's folder, else the registered file's folder
    root = showcase.root if showcase is not None else Path(product["path"]).parent
    path = _inside(product["path"], folder)
    return {
        "id": product["id"],
        "title": product["title"],
        "summary": product["summary"],
        "status": product["status"],
        "path": path,
        "root": _inside(root, folder) if path is not None else None,
        "problem": "outside_project" if path is None else None,
        "reviews": [
            {
                "at": review.get("at"),
                "verdict": review.get("verdict"),
                "notes": review.get("notes", ""),
            }
            for review in product["reviews"][-3:]
        ],
        "offers": [
            offer["id"]
            for offer in st.offers.values()
            if offer["product"] == product["id"]
        ],
        "descriptor": shop.descriptor_report(product),
    }


def _attention(
    st: journal.State, payment_text: bool, product: str | None = None
) -> list[dict[str, Any]]:
    """What waits for a person, derived from the state alone: repeated on every call
    until the state no longer has it. ``product`` is the step's product."""
    from corporation import shop

    items: list[dict[str, Any]] = []
    # what the shop itself answers; a reply with any other text was written by a role
    own = {
        shop.ACK_ORDER,
        shop.ACK_ORDER_WAIT,
        *(answer for _said, answer in shop.SIGNALS.values()),
    }
    for cid, order in st.orders.items():
        client = st.clients.get(cid, {})
        since = order["signals"].get("paid")
        if since and order["product"] not in client.get("paid", []):
            items.append(
                {
                    "kind": "payment_check",
                    "client": cid,
                    "product": order["product"],
                    "amount": order["amount"],
                    "currency": order["currency"],
                    "since": since,
                }
            )
    for cid, order in st.orders.items():
        since = order["signals"].get("help")
        if since and not any(
            message["from"] != "client"
            and message["at"] >= since
            and message["text"] not in own
            for message in st.clients.get(cid, {}).get("messages", [])
        ):
            items.append(
                {
                    "kind": "help_request",
                    "client": cid,
                    "product": order["product"],
                    "has_contact": bool(order["contact"]),
                    "since": since,
                }
            )
    for cid, order in st.orders.items():
        pid = order["product"]
        item = st.products.get(pid or "")
        if (
            item is None
            or pid not in st.clients.get(cid, {}).get("paid", [])
            or any(d["client"] == cid and d["product"] == pid for d in st.deliveries)
        ):
            continue
        # paid and not downloaded yet: the download answers 503 while the product's
        # descriptor is broken or its registered file is not in place
        report = shop.descriptor_report(item)
        if shop._placed(item) is None or (report["present"] and not report["ok"]):
            items.append(
                {
                    "kind": "download_blocked",
                    "client": cid,
                    "product": pid,
                    "errors": report["errors"],
                }
            )
    broken: dict[str, list[str]] = {}
    for item in shop._shelf(st):
        if item["problems"]:
            broken.setdefault(item["product"]["id"], item["problems"])
    for pid, problems in broken.items():
        # on the shelf with a descriptor the shop cannot read: no demo, no download
        items.append({"kind": "descriptor_broken", "product": pid, "errors": problems})
    unpaid = _unpaid(st)
    if not payment_text and (unpaid or st.channels.get(_SITE, {}).get("enabled")):
        # payment text is not set: buyers cannot pay
        items.append({"kind": "payment_text_missing", "orders": len(unpaid)})
    for name, channel in st.channels.items():
        if not channel.get("enabled") and channel.get("reason") not in (
            None,
            "",
            "owner",
        ):
            items.append(
                {
                    "kind": "channel_stopped",
                    "channel": name,
                    "since": channel.get("since"),
                    "reason": channel["reason"],
                }
            )
    waiting = [
        client["messages"][-1]["at"]
        for client in st.clients.values()
        if client["messages"]
        and client["messages"][-1]["from"] == "client"
        and st.channels.get(client.get("channel") or "telegram", {}).get("enabled")
    ]
    if waiting:
        items.append(
            {"kind": "client_waiting", "count": len(waiting), "since": min(waiting)}
        )
    for project in st.projects.values():
        if project["status"] == "open" and not project["tasks"]:
            items.append({"kind": "project_without_tasks", "project": project["id"]})
    for item in st.products.values():
        if (item["status"] == "ready" or item["id"] == product) and _misplaced(item):
            # registered outside its project's folder: an attended session must look
            items.append({"kind": "product_path", "product": item["id"]})
    if st.cycle.get("running"):
        items.append({"kind": "local_cycle"})
    return items


def next_step(data: dict[str, Any]) -> dict[str, Any]:
    """What the company does next, for a worker outside the box.

    The cycle's own scheduling without the cycle's turn: the handoffs open the
    pipeline's tasks, ``_pick`` names the role and the task, ``_brief`` writes the
    brief. No model is asked, the runner is not touched, the cycle is not started; with
    ``dry`` nothing is written.

    ``data``: optional dry (look only) and skip (the keys of the steps the worker set
    aside). Every key of the answer is always present; no text a buyer wrote is in it.
    """
    from corporation import channels, cycle, shop

    dry = bool(data.get("dry"))
    asked = data.get("skip")
    skip = (
        {k for k in asked if isinstance(k, str)} if isinstance(asked, list) else set()
    )
    skipped: set[str] = set()
    plan: journal.State | None = None
    with _STEP_LOCK:
        st = journal.state()
        before = set(st.tasks)
        running = bool(st.cycle.get("running"))
        picked = None
        pending: int | None = None  # the directive the director's step is about
        if not running:
            if not dry:
                with journal.embedding_deferred():
                    cycle._handoffs(st)
                # _handoffs leaves stub tasks in the state it was given
                st = journal.state()
            # a copy to plan on: what the worker set aside is taken out of it
            plan = journal.state()
            for tid in [
                t
                for t, item in plan.tasks.items()
                if item["status"] == "queued" and _key(item, plan.orders) in skip
            ]:
                skipped.add(_key(plan.tasks[tid], plan.orders))
                del plan.tasks[tid]
            for directive in plan.directives:
                key = f"director|{directive['seq']}"
                if not directive.get("project") and key in skip:
                    skipped.add(key)
                    directive["project"] = "set-aside"
            # the local runner's batching by role does not apply outside
            plan.cycle["last_role"] = None
            picked = cycle._pick(plan)
            pending = next(
                (d["seq"] for d in plan.directives if not d.get("project")), None
            )
    opened = sorted(set(st.tasks) - before)

    # The answer is built from st and from the pick; of the planning copy only the
    # brief of a step without a task is used.
    name, planned = picked if picked is not None else (None, None)
    role = get_role(name) if name is not None else None
    task = st.tasks.get(planned["id"], planned) if planned is not None else None
    kind = step = key = None
    directive_view = None
    if task is not None:
        kind, step, key = "task", f"task:{task['id']}", _key(task, st.orders)
    elif name == "director":
        kind, step, key = "director", f"director:{pending}", f"director|{pending}"
        found = next((d for d in st.directives if d["seq"] == pending), None)
        if found is not None:
            directive_view = {
                k: found.get(k) for k in ("seq", "at", "text", "by", "why")
            }
    elif name == "founder":
        kind, step, key = "founder", f"founder:{len(st.directives)}", "founder"

    # No text a buyer wrote leaves here: the handoff quotes the buyer's last message in
    # the task's text, and the brief repeats the text.
    client = st.clients.get(task.get("client") or "") if task is not None else None
    with_client = bool(task is not None and task.get("client"))
    brief = ""
    if role is not None and task is None and plan is not None:
        # The director reads the directives without the ones set aside; the founder
        # reads the failed tasks of clients without their result text.
        for item in plan.tasks.values():
            if item["status"] == "failed" and item.get("client"):
                item["result"] = ""
        brief = cycle._brief(plan, role, None)
    elif role is not None and not with_client:
        brief = cycle._brief(st, role, task)
    offer = st.offers.get(task.get("offer") or "") if task is not None else None

    payment_text = bool(os.environ.get("LOGOS_CORP_PAYMENT_TEXT"))
    on_sale = {_id(item["product"]) for item in shop._shelf(st)}
    shifts = [n for n in st.notes if n.get("shift") == "outside"]
    return {
        "idle": picked is None,
        "why": "" if picked is not None else ("local_cycle" if running else "empty"),
        "dry": dry,
        "now": journal._now(),
        "step": step,
        "key": key,
        "kind": kind,
        "role": name,
        "role_title": role.title if role is not None else None,
        "hired": name in hired_roles() if name is not None else None,
        "instructions": role.instructions if role is not None else None,
        "brief": brief if picked is not None else None,
        "task": {
            "id": task["id"],
            "project": task.get("project"),
            "text": "" if with_client else task.get("text", ""),
            "product": task.get("product"),
            "offer": task.get("offer"),
            "client": _label(task.get("client"), st.orders) or None,
            "turns": task.get("turns", 0),
            "opened": task.get("opened"),
        }
        if task is not None
        else None,
        "directive": directive_view,
        "project": _project_view(st, task.get("project")) if task is not None else None,
        "product": _product_view(st, task.get("product")) if task is not None else None,
        "offer": {
            k: offer.get(k)
            for k in (
                "id",
                "product",
                "project",
                "price",
                "currency",
                "channel",
                "status",
                "text",
            )
        }
        if offer is not None
        else None,
        "client": {
            "id": _label(client["id"], st.orders),
            "channel": client.get("channel"),
            "paid": list(client["paid"]),
            "messages": len(client["messages"]),
        }
        if client is not None
        else None,
        "channels": {
            "enabled": sorted(n for n, c in st.channels.items() if c.get("enabled")),
            "configured": channels.configured(),
            "payment_text": payment_text,  # a flag, never the text
        },
        # the rules a text that leaves through a channel is judged by
        "gate": {"patterns": [p.pattern for p in channels._SECRET_PATTERNS]},
        "company": {
            "records": st.records,
            "queued": sum(1 for t in st.tasks.values() if t["status"] == "queued"),
            "orders": len(st.orders),
            "orders_unpaid": len(_unpaid(st)),
            "last_directive_at": st.directives[-1]["at"] if st.directives else None,
            "roles": list(all_roles()),
        },
        "shelf": [
            {
                "product": product["id"],
                "offers": [
                    o["id"]
                    for o in st.offers.values()
                    if o["product"] == product["id"] and o["channel"] == _SITE
                ],
                "on_sale": product["id"] in on_sale,
                "descriptor": shop.descriptor_report(product),
                "problem": "outside_project" if _misplaced(product) else None,
            }
            for product in st.products.values()
            if product["status"] == "ready"
        ],
        "attention": _attention(
            st, payment_text, task.get("product") if task is not None else None
        ),
        "outside": {
            "shifts": len(shifts),
            "last_at": shifts[-1]["at"] if shifts else None,
            "last_role": shifts[-1].get("actor") if shifts else None,
        },
        "local_cycle": {
            "running": running,
            "thread_alive": bool(
                cycle._thread is not None and cycle._thread.is_alive()
            ),
        },
        "skipped": sorted(skipped),
        "opened": opened,
    }


# ----- a project's files ----------------------------------------------------------------------


def project_files(data: dict[str, Any]) -> dict[str, Any]:
    """The files of a project's products folder: the list, and the named ones' content.

    ``data``: project, optional paths. The list is every regular file under the
    project's folder, links left out; a name is served only when it is exactly one of
    the listed paths, and its hash and content come from one read.
    """
    from corporation.tools import _project_dir

    project = str(data.get("project") or "")
    st = journal.state()
    if project not in st.projects:
        raise ValueError(
            f"no such project: {project}; projects: {', '.join(st.projects) or 'none'}"
        )
    asked = data.get("paths")
    wanted = (
        {p for p in asked if isinstance(p, str)} if isinstance(asked, list) else set()
    )
    folder = _project_dir(project)
    root = folder.resolve()
    if folder.is_symlink() or root.parent != journal.products_dir().resolve():
        raise ValueError(f"the folder of project {project} is not a products folder")
    files: list[dict[str, Any]] = []
    content: list[dict[str, str]] = []
    # under the lock a list is never taken in the middle of a shift's writes
    with _STEP_LOCK:
        found: list[Path] = []
        for top, dirs, names in os.walk(folder, followlinks=False):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(top, d))]
            for name in names:
                path = Path(top, name)
                if os.path.islink(path) or not stat.S_ISREG(os.lstat(path).st_mode):
                    continue
                try:
                    path.resolve().relative_to(root)
                except ValueError:
                    continue
                found.append(path)
        for path in sorted(found):
            name = path.relative_to(folder).as_posix()
            payload = path.read_bytes()
            files.append(
                {
                    "path": name,
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
            if name in wanted:
                content.append(
                    {
                        "path": name,
                        "content_base64": base64.b64encode(payload).decode("ascii"),
                    }
                )
    return {"project": project, "dir": folder.name, "files": files, "content": content}


# ----- the shift ------------------------------------------------------------------------------


def _step_open(st: journal.State, step: str, role: str, task: str | None) -> bool:
    """Whether the step ``next`` handed out is still to be done, by this role."""
    kind, _, key = step.partition(":")
    if kind == "task":
        item = st.tasks.get(key) or {}
        return (
            (task is None or task == key)
            and item.get("status") == "queued"
            and item.get("role") == role
        )
    if kind == "director":
        return role == "director" and any(
            str(d["seq"]) == key and not d.get("project") for d in st.directives
        )
    if kind == "founder":
        return role == "founder" and key == str(len(st.directives))
    return False


def _step_bound(
    st: journal.State,
    step: str,
    project: str | None,
    task: str | None,
    calls: list[Any],
) -> None:
    """A shift that names its step works on that step only: its task, its project,
    its directive. Raises ``ValueError`` otherwise; nothing is written before."""
    if st.cycle.get("running"):
        raise ValueError(
            "the local cycle is running: it owns the queue; outside shifts wait"
        )
    kind, _, key = step.partition(":")
    item: dict[str, Any] = {}
    if kind == "task":
        item = st.tasks.get(key) or {}
        if task != key:
            raise ValueError(f"step {step} is for task {key}")
        if item.get("project") and project != item["project"]:
            raise ValueError(f"task {key} belongs to project {item['project']}")
        owner = st.projects.get(item.get("project") or "", {})
        if owner.get("status", "open") != "open":
            raise ValueError(f"the project of task {key} is closed")
    for call in calls:
        args = call.get("arguments") if isinstance(call, dict) else None
        if not isinstance(args, dict):
            continue
        if kind == "task" and args.get("task") not in (None, key):
            raise ValueError(f"a call of step {step} names another task")
        if kind == "task" and args.get("project") not in (None, item.get("project")):
            raise ValueError(f"a call of step {step} names another project")
        if (
            kind == "director"
            and call.get("tool") == "open_project"
            and str(args.get("directive")) != key
        ):
            raise ValueError(f"step {step} opens a project for directive {key} only")
        if kind != "task" and args.get("project") not in (None, "@opened"):
            raise ValueError(f"step {step} works only in the project it opens")


def _opened(call: Any, opened_project: str | None) -> Any:
    """The call with ``"@opened"`` as its project replaced by the project this shift
    opened."""
    if opened_project is None or not isinstance(call, dict):
        return call
    arguments = call.get("arguments")
    if not isinstance(arguments, dict) or arguments.get("project") != "@opened":
        return call
    return {**call, "arguments": {**arguments, "project": opened_project}}


def run_shift(data: dict[str, Any]) -> dict[str, Any]:
    """Do one outside shift: write the files, make the calls, leave the note.

    ``data``: role, brain, optional project, task and step (the one ``next`` handed
    out), files [{path, content}] (text) or [{path, content_base64}] (anything else: an
    icon, an archive), calls [{category, tool, arguments}], reply (what the worker says
    it did). The calls run in order and stop at the first one a tool refuses; the answer
    says how far it got. A call whose ``arguments.project`` is ``"@opened"`` gets the
    project the latest ``open_project`` of the same shift opened.
    """
    role = str(data.get("role") or "")
    if get_role(role) is None:
        raise ValueError(f"no such employee: {role}")
    brain = str(data.get("brain") or "").strip()
    if not brain:
        raise ValueError("brain is required: name the model that does this shift")
    project = str(data.get("project") or "") or None
    task = str(data.get("task") or "") or None
    step = str(data.get("step") or "").strip() or None
    files = data.get("files") or []
    calls = data.get("calls") or []
    if not isinstance(files, list) or not isinstance(calls, list):
        raise ValueError("files and calls are lists")
    # One shift at a time, and never beside ``next``; the caller does not wait for the
    # runner once per record.
    with _STEP_LOCK, journal.embedding_deferred():
        st = journal.state()
        if project is not None and project not in st.projects:
            raise ValueError(
                f"no such project: {project}; projects: {', '.join(st.projects) or 'none'}"
            )
        if files and project is None:
            raise ValueError("files need a project: they go into its products folder")
        if task is not None and task not in st.tasks:
            raise ValueError(f"no such task: {task}")
        # a step is taken once: checked before any file is written or call made
        if step is not None and not _step_open(st, step, role, task):
            raise ValueError(f"step {step} is already taken; ask next again")
        if step is not None:
            _step_bound(st, step, project, task, calls)
        # every file is checked before the first one is written
        checked = [_check_file(project or "", item) for item in files]

        role_token = context.set_role(role)
        brain_token = context.set_brain(brain)
        try:
            written: list[str] = []
            results: list[dict[str, Any]] = []
            opened_project: str | None = None
            # a file that cannot be written stops the shift; the note still says how
            # far it got
            file_error = ""
            try:
                for target, payload in checked:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(payload)
                    written.append(str(target))
            except OSError as exc:
                file_error = _redact(f"files: {type(exc).__name__}: {exc}")
            for call in calls if not file_error else []:
                outcome = _call(_opened(call, opened_project))
                results.append(outcome)
                if not outcome["success"]:
                    break
                if (outcome["category"], outcome["tool"]) == (
                    "corporation",
                    "open_project",
                ):
                    opened_project = str(outcome["result"]["project"])
            failed = next((r for r in results if not r["success"]), None)
            note = journal.ledger().append(
                "note",
                role,
                {
                    "text": str(data.get("reply") or "")[:4000]
                    or f"смена вне ящика: {len(written)} файлов, {len(results)} вызовов",
                    "shift": "outside",
                    "step": step or "",
                    "files": " ".join(written),
                    "calls": " ".join(f"{r['category']}.{r['tool']}" for r in results),
                    "stopped_at": f"{failed['category']}.{failed['tool']}: {failed['error']}"
                    if failed
                    else file_error,
                },
                project=project,
                task=task,
            )
        finally:
            context.reset_brain(brain_token)
            context.reset(role_token)
    return {
        "role": role,
        "brain": brain,
        "step": step,
        "files": written,
        "results": results,
        "calls_asked": len(calls),
        "calls_made": len(results),
        "complete": not file_error and failed is None and len(results) == len(calls),
        "note": note["seq"],
    }
