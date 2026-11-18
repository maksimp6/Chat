"""The company's ledger: an append-only journal with a hash chain, and the state that
is derived from it on every read.

Nothing about the company lives anywhere else. A record is written (and fsynced)
before the step it describes counts as done; the hash of the previous record is in
every record, so a lost or edited line is found by ``verify`` at start-up. Every
record is also embedded into the vector database (collection ``corporation``), so
the roles can search their own past by meaning; an embedding that failed is
made up by the ledger's worker (``catch_up``). Snapshots (``snapshot``) copy the journal and the
products to the phone's /sdcard and to the state directory.

The file is ``<state dir>/corporation/ledger.jsonl``; the state directory is the
one the deploy keeps (``LOGOS_STATE_DIR``, ``~/logos-local-state`` on the phone).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import shutil
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from corporation import context

logger = logging.getLogger("logos.corporation.ledger")

__all__ = [
    "Ledger",
    "State",
    "embedding_deferred",
    "ledger",
    "products_dir",
    "staff_dir",
    "state",
    "state_dir",
]

_ZERO = "0" * 64
COLLECTION = "corporation"

KINDS = frozenset(
    {
        "board.directive",
        "board.decision",
        "board.money",
        "board.command",
        "board.goal",
        "project.open",
        "project.close",
        "project.directive",
        "task.open",
        "task.start",
        "task.turn",
        "task.done",
        "task.failed",
        "product.draft",
        "product.review",
        "product.ready",
        "offer.draft",
        "offer.published",
        "client.message",
        "client.reply",
        "invoice",
        "delivery",
        "revenue.proposed",
        "revenue.confirmed",
        "expense",
        "court.case",
        "channel.stop",
        "channel.start",
        "channel.cursor",
        "cycle.start",
        "cycle.stop",
        "cycle.tick",
        "cycle.error",
        "model.switch",
        "staff.file",
        "staff.hire",
        "note",
        "backup",
    }
)

_EMBED_LATER: ContextVar[bool] = ContextVar("corporation_embed_later", default=False)


@contextmanager
def embedding_deferred() -> Iterator[None]:
    """Records appended inside are embedded by the ledger's own worker thread, not by
    the caller: a visitor of the shop and a caller of the door do not wait for the runner."""
    token = _EMBED_LATER.set(True)
    try:
        yield
    finally:
        _EMBED_LATER.reset(token)


def state_dir() -> Path:
    configured = os.environ.get("LOGOS_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    memory = os.environ.get("LOGOS_MEMORY_PATH")
    if memory:
        return Path(memory).expanduser().resolve().parent
    return Path.home() / "logos-local-state"


def products_dir() -> Path:
    """Where production writes the products: a directory the deploy keeps."""
    return state_dir() / "corporation" / "products"


def staff_dir() -> Path:
    """Where the personnel files live as files (``<role>.md``), next to the products.

    Owner, 09.11.2026: the embeddings database "is not replace files manager" — the
    vector copy is an index, a file is a file."""
    return state_dir() / "corporation" / "staff"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _hash(prev: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256(prev.encode("ascii") + _canonical(payload)).hexdigest()


def _entry_text(entry: dict[str, Any]) -> str:
    """The record as the embedder reads it: what happened, who, about what."""
    data = entry.get("data") or {}
    parts = [f"{entry['kind']} — {entry.get('actor') or ''}"]
    for key in ("project", "task", "product", "offer"):
        if entry.get(key):
            parts.append(f"{key} {entry[key]}")
    for key, value in data.items():
        if isinstance(value, (str, int, float)) and str(value).strip():
            parts.append(f"{key}: {value!s}")
    return "\n".join(parts)


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._entries: list[dict[str, Any]] | None = None
        self._pending: queue.SimpleQueue[dict[str, Any] | None] = queue.SimpleQueue()
        self._embedder: threading.Thread | None = None

    # ----- reading -------------------------------------------------------------

    def entries(self) -> list[dict[str, Any]]:
        """Every record, in order (cached; the cache is kept current by append)."""
        with self._lock:
            if self._entries is None:
                self._entries = self._read()
            return list(self._entries)

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        result: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    result.append(json.loads(line))
                except ValueError:
                    logger.error("[CORP] ledger line %d is not JSON", len(result) + 1)
                    raise
        return result

    def tail(self, count: int = 50) -> list[dict[str, Any]]:
        return self.entries()[-count:]

    def last(self) -> dict[str, Any] | None:
        items = self.entries()
        return items[-1] if items else None

    def verify(self) -> dict[str, Any]:
        """Recompute the chain. ``ok`` is false at the first broken record."""
        prev = _ZERO
        for entry in self.entries():
            payload = {key: value for key, value in entry.items() if key != "hash"}
            if entry.get("prev") != prev or _hash(prev, payload) != entry.get("hash"):
                return {
                    "ok": False,
                    "broken_at": entry.get("seq"),
                    "records": len(self._entries or []),
                }
            prev = entry["hash"]
        return {"ok": True, "records": len(self._entries or []), "hash": prev}

    # ----- writing -------------------------------------------------------------

    def append(
        self,
        kind: str,
        actor: str,
        data: dict[str, Any] | None = None,
        *,
        project: str | None = None,
        task: str | None = None,
        product: str | None = None,
        offer: str | None = None,
    ) -> dict[str, Any]:
        """Write one record, fsync it, embed it. Returns the record."""
        return self.append_all(
            [
                {
                    "kind": kind,
                    "actor": actor,
                    "data": data,
                    "project": project,
                    "task": task,
                    "product": product,
                    "offer": offer,
                }
            ]
        )[0]

    def append_all(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Write several records as one group: consecutive numbers, one write, one
        fsync; then embed each. Every item holds the arguments of ``append`` by name
        (``kind``, ``actor``, ``data``, ``project``, ``task``, ``product``, ``offer``).
        Returns the records."""
        for record in records:
            if record["kind"] not in KINDS:
                raise ValueError(f"unknown ledger kind: {record['kind']}")
        if not records:
            return []
        # A shift done by a worker outside the box names its model in every record it
        # writes: the journal never passes outside work off as the local runner's.
        outside_brain = context.brain()
        written: list[dict[str, Any]] = []
        with self._lock:
            items = self.entries()
            prev = items[-1]["hash"] if items else _ZERO
            for record in records:
                record_data = dict(record.get("data") or {})
                if outside_brain and "via" not in record_data:
                    record_data["via"] = outside_brain
                payload: dict[str, Any] = {
                    "seq": len(items) + len(written) + 1,
                    "at": _now(),
                    "kind": record["kind"],
                    "actor": record["actor"],
                    "data": record_data,
                    "prev": prev,
                }
                for key in ("project", "task", "product", "offer"):
                    if record.get(key):
                        payload[key] = record[key]
                entry = {**payload, "hash": _hash(prev, payload)}
                written.append(entry)
                prev = entry["hash"]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(
                    "".join(
                        json.dumps(entry, ensure_ascii=False) + "\n"
                        for entry in written
                    )
                )
                stream.flush()
                os.fsync(stream.fileno())
            self._entries = items + written
        for entry in written:
            if _EMBED_LATER.get():
                self._embed_later(entry)
            else:
                self._embed(entry)
        return written

    # ----- the vector copy -------------------------------------------------------

    @staticmethod
    def _doc_id(entry: dict[str, Any]) -> str:
        return f"seq-{int(entry['seq']):08d}"

    def _embed(self, entry: dict[str, Any]) -> bool:
        try:
            from vector_db import store as vectors

            vectors.add(
                COLLECTION,
                _entry_text(entry),
                doc_id=self._doc_id(entry),
                metadata={
                    "kind": entry["kind"],
                    "actor": entry.get("actor"),
                    "at": entry["at"],
                    "project": entry.get("project"),
                    "task": entry.get("task"),
                },
            )
            return True
        except (
            Exception
        ) as exc:  # the journal is the truth; the vector copy catches up later
            logger.warning(
                "[CORP] record %s not embedded yet: %s", entry.get("seq"), exc
            )
            return False

    def _embed_later(self, entry: dict[str, Any] | None) -> None:
        self._pending.put(entry)
        with self._lock:
            if self._embedder is None or not self._embedder.is_alive():
                self._embedder = threading.Thread(
                    target=self._embed_pending,
                    name="logos-corporation-embed",
                    daemon=True,
                )
                self._embedder.start()

    def catch_up(self) -> None:
        """Wake the ledger's worker to embed what the vector database does not hold yet."""
        self._embed_later(None)

    def _embed_pending(self) -> None:
        behind = True  # a fresh process may have lost a queue: look once
        while True:
            entry = self._pending.get()
            ok = self._embed(entry) if entry is not None else True
            if not ok:
                behind = True
            elif behind:
                behind = not self._catch_up()

    def _catch_up(self) -> bool:
        """Embed every record the vector database does not hold yet. False when a
        look-up or an embedding failed: the next record or wake-up tries again."""
        try:
            from vector_db import store as vectors
        except Exception:
            return False
        for entry in self.entries():
            try:
                present = vectors.has(COLLECTION, self._doc_id(entry))
            except Exception:
                return False
            if not present and not self._embed(entry):
                return False
        return True

    def reembed_missing(self, limit: int = 200) -> int:
        """Embed the records the vector database does not hold yet (after a runner outage)."""
        try:
            from vector_db import store as vectors
        except Exception:
            return 0
        done = 0
        for entry in self.entries():
            if done >= limit:
                break
            try:
                present = vectors.has(COLLECTION, self._doc_id(entry))
            except Exception:
                return done
            if not present:
                if not self._embed(entry):
                    return done
                done += 1
        return done

    # ----- snapshots ---------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """The journal and the products copied to the places that survive the phone:
        ``<state>/corporation/snapshots/<stamp>/`` and ``/sdcard/logos-corporation/<stamp>/``."""
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        targets = [state_dir() / "corporation" / "snapshots" / stamp]
        sdcard = Path("/sdcard")
        if sdcard.is_dir():
            targets.append(sdcard / "logos-corporation" / stamp)
        written: list[str] = []
        for target in targets:
            try:
                target.mkdir(parents=True, exist_ok=True)
                with self._lock:  # no record is written while the journal is copied
                    if self.path.exists():
                        shutil.copy2(self.path, target / self.path.name)
                if products_dir().is_dir():
                    shutil.copytree(
                        products_dir(), target / "products", dirs_exist_ok=True
                    )
                if staff_dir().is_dir():
                    shutil.copytree(staff_dir(), target / "staff", dirs_exist_ok=True)
                written.append(str(target))
            except OSError as exc:
                logger.warning("[CORP] snapshot to %s failed: %s", target, exc)
        verify = self.verify()
        return {
            "stamp": stamp,
            "targets": written,
            "records": verify.get("records"),
            "ok": verify.get("ok"),
        }


# ----- the state, derived from the journal ---------------------------------------------


@dataclass
class State:
    directives: list[dict[str, Any]] = field(default_factory=list)
    projects: dict[str, dict[str, Any]] = field(default_factory=dict)
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    products: dict[str, dict[str, Any]] = field(default_factory=dict)
    offers: dict[str, dict[str, Any]] = field(default_factory=dict)
    clients: dict[str, dict[str, Any]] = field(default_factory=dict)
    money: dict[str, Any] = field(
        default_factory=lambda: {
            "proposed": 0.0,
            "confirmed": 0.0,
            "expenses": 0.0,
            "sales": [],
        }
    )
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)
    cycle: dict[str, Any] = field(
        default_factory=lambda: {
            "running": False,
            "steps": 0,
            "last_tick": None,
            "last_role": None,
            "model": None,
        }
    )
    decisions: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    records: int = 0
    last_backup: dict[str, Any] | None = None
    goal: str | None = (
        None  # the standing goal (board.goal); roles.DEFAULT_GOAL when none
    )
    last_founder: str | None = None  # when the founder last had a shift
    offices: dict[str, str] = field(
        default_factory=dict
    )  # conversation id -> role (an employee's office dialog)
    office_of: dict[str, str] = field(default_factory=dict)  # role -> conversation id
    court: list[dict[str, Any]] = field(
        default_factory=list
    )  # court.case records: the reject chute
    deliveries: list[dict[str, Any]] = field(
        default_factory=list
    )  # delivery records: boxes shipped
    published: list[dict[str, Any]] = field(
        default_factory=list
    )  # offer.published: boxes on the shelf
    staff: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )  # role -> the latest personnel file (staff.file, written by the employee)
    hired: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )  # role key -> the definition of an employee HR hired (staff.hire)
    invoices: list[dict[str, Any]] = field(
        default_factory=list
    )  # invoice records: what was billed to whom
    orders: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )  # site client id -> the order that created it

    def next_id(self, prefix: str, table: dict[str, Any]) -> str:
        width = {"p": 4, "t": 5, "pr": 4, "o": 4}.get(prefix, 4)
        return f"{prefix}-{len(table) + 1:0{width}d}"

    def open_tasks(self) -> list[dict[str, Any]]:
        return [
            task
            for task in self.tasks.values()
            if task["status"] in ("queued", "working")
        ]

    def summary(self) -> dict[str, Any]:
        return {
            "records": self.records,
            "cycle": self.cycle,
            "directives": len(self.directives),
            "directives_open": sum(1 for d in self.directives if not d.get("project")),
            "projects": {
                status: sum(1 for p in self.projects.values() if p["status"] == status)
                for status in ("open", "closed")
            },
            "tasks": {
                status: sum(1 for t in self.tasks.values() if t["status"] == status)
                for status in ("queued", "working", "done", "failed")
            },
            "products": {
                status: sum(1 for p in self.products.values() if p["status"] == status)
                for status in ("draft", "rework", "ready")
            },
            "offers": {
                status: sum(1 for o in self.offers.values() if o["status"] == status)
                for status in ("draft", "published")
            },
            "clients": len(self.clients),
            "staff": len(self.staff),
            "hired": len(self.hired),
            "money": {
                key: self.money[key] for key in ("proposed", "confirmed", "expenses")
            },
            "channels": self.channels,
            "last_backup": self.last_backup,
        }

    def brief(self, limit_chars: int = 1500) -> str:
        """The company's situation for a role's prompt: short, Russian, facts only."""
        from corporation.roles import DEFAULT_GOAL

        lines = []
        money = self.money
        lines.append(f"Цель компании: {self.goal or DEFAULT_GOAL}")
        lines.append(
            f"Касса: предложено {money['proposed']:.0f}, подтверждено {money['confirmed']:.0f}, расходы {money['expenses']:.0f} RUB."
        )
        open_dirs = [d for d in self.directives if not d.get("project")]
        if open_dirs:
            lines.append(
                "Директивы без проекта: "
                + "; ".join(f"[{d['seq']}] {d['text'][:200]}" for d in open_dirs[:5])
            )
        for pid, project in list(self.projects.items())[-6:]:
            if project["status"] != "open":
                continue
            tasks = [self.tasks[t] for t in project["tasks"] if t in self.tasks]
            lines.append(
                f"Проект {pid} «{project['title']}»: задач {len(tasks)} "
                f"(в очереди {sum(1 for t in tasks if t['status'] == 'queued')}, сделано {sum(1 for t in tasks if t['status'] == 'done')}, "
                f"провалено {sum(1 for t in tasks if t['status'] == 'failed')}); продуктов {sum(1 for p in self.products.values() if p['project'] == pid)}."
            )
        text = "\n".join(lines)
        return text


def _money(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_state(entries: list[dict[str, Any]]) -> State:
    st = State()
    for e in entries:
        kind, data = e["kind"], e.get("data") or {}
        st.records = e["seq"]
        if kind == "board.directive":
            st.directives.append(
                {
                    "seq": e["seq"],
                    "at": e["at"],
                    "text": data.get("text", ""),
                    "project": None,
                    "by": e.get("actor"),
                    "why": data.get("why"),
                }
            )
        elif kind == "board.goal":
            st.goal = data.get("text") or None
        elif kind == "board.decision":
            st.decisions.append({"seq": e["seq"], "at": e["at"], **data})
            target = data.get("target")
            if target in st.tasks and data.get("verdict") == "requeue":
                st.tasks[target]["status"] = "queued"
            if target in st.projects and data.get("verdict") == "close":
                st.projects[target]["status"] = "closed"
        elif kind == "project.open":
            pid = data["id"]
            st.projects[pid] = {
                "id": pid,
                "title": data.get("title", ""),
                "goal": data.get("goal", ""),
                "status": "open",
                "tasks": [],
                "opened": e["at"],
                "directive": data.get("directive"),
                "conversation": data.get("conversation"),
                "line": data.get("line") or "texts",
            }
            for d in st.directives:
                if d["seq"] == data.get("directive"):
                    d["project"] = pid
        elif kind == "project.close":
            if data.get("id") in st.projects:
                st.projects[data["id"]]["status"] = "closed"
                st.projects[data["id"]]["result"] = data.get("result")
        elif kind == "project.directive":
            pid, seq = data.get("id"), data.get("directive")
            if pid in st.projects:
                st.projects[pid]["directive"] = seq
                for d in st.directives:
                    if d["seq"] == seq:
                        d["project"] = pid
        elif kind == "task.open":
            tid = data["id"]
            st.tasks[tid] = {
                "id": tid,
                "project": e.get("project"),
                "role": data.get("role"),
                "text": data.get("text", ""),
                "status": "queued",
                "turns": 0,
                "opened": e["at"],
                "result": None,
                "product": data.get("product"),
                "offer": data.get("offer"),
                "client": data.get("client"),
            }
            if e.get("project") in st.projects:
                st.projects[e["project"]]["tasks"].append(tid)
        elif kind == "task.start":
            if e.get("task") in st.tasks:
                st.tasks[e["task"]]["status"] = "working"
        elif kind == "task.turn":
            if e.get("task") in st.tasks:
                st.tasks[e["task"]]["turns"] += 1
                st.tasks[e["task"]]["last_reply"] = (data.get("reply") or "")[:500]
        elif kind == "task.done":
            if e.get("task") in st.tasks:
                st.tasks[e["task"]].update(
                    status="done", result=data.get("result"), closed=e["at"]
                )
        elif kind == "task.failed":
            if e.get("task") in st.tasks:
                st.tasks[e["task"]].update(
                    status="failed", result=data.get("reason"), closed=e["at"]
                )
        elif kind == "product.draft":
            prid = data["id"]
            st.products[prid] = {
                "id": prid,
                "project": e.get("project"),
                "task": e.get("task"),
                "title": data.get("title", ""),
                "path": data.get("path", ""),
                "summary": data.get("summary", ""),
                "status": "draft",
                "reviews": [],
                "at": e["at"],
            }
        elif kind == "product.review":
            if e.get("product") in st.products:
                st.products[e["product"]]["reviews"].append({"at": e["at"], **data})
                st.products[e["product"]]["status"] = (
                    "ready" if data.get("verdict") == "ready" else "rework"
                )
        elif kind == "product.ready":
            if e.get("product") in st.products:
                st.products[e["product"]]["status"] = "ready"
        elif kind == "offer.draft":
            oid = data["id"]
            st.offers[oid] = {
                "id": oid,
                "product": e.get("product"),
                "project": e.get("project"),
                "price": _money(data.get("price")),
                "currency": data.get("currency", "RUB"),
                "channel": data.get("channel"),
                "text": data.get("text", ""),
                "status": "draft",
                "at": e["at"],
            }
            st.money["proposed"] += _money(data.get("price"))
        elif kind == "offer.published":
            if e.get("offer") in st.offers:
                st.offers[e["offer"]].update(
                    status="published",
                    published=e["at"],
                    message_id=data.get("message_id"),
                )
            st.published.append(
                {
                    "at": e["at"],
                    "offer": e.get("offer"),
                    "product": e.get("product"),
                    "channel": data.get("channel"),
                }
            )
        elif kind == "delivery":
            st.deliveries.append(
                {
                    "at": e["at"],
                    "product": e.get("product"),
                    "client": data.get("client"),
                    "channel": data.get("channel"),
                }
            )
        elif kind == "court.case":
            st.court.append(
                {
                    "seq": e["seq"],
                    "at": e["at"],
                    "task": e.get("task") or data.get("task"),
                    "project": e.get("project"),
                    "reason": data.get("reason"),
                    "channel": data.get("channel"),
                    "action": data.get("action"),
                }
            )
        elif kind == "client.message":
            cid = str(data.get("client"))
            client = st.clients.setdefault(
                cid,
                {
                    "id": cid,
                    "name": data.get("name"),
                    "channel": data.get("channel"),
                    "messages": [],
                    "paid": [],
                },
            )
            client["messages"].append(
                {
                    "at": e["at"],
                    "text": data.get("text", ""),
                    "from": "client",
                    "channel": data.get("channel"),
                }
            )
        elif kind == "client.reply":
            cid = str(data.get("client"))
            client = st.clients.setdefault(
                cid,
                {
                    "id": cid,
                    "name": None,
                    "channel": data.get("channel"),
                    "messages": [],
                    "paid": [],
                },
            )
            client["messages"].append(
                {
                    "at": e["at"],
                    "text": data.get("text", ""),
                    "from": "sales",
                    "channel": data.get("channel"),
                }
            )
        elif kind == "revenue.confirmed":
            st.money["confirmed"] += _money(data.get("amount"))
            st.money["sales"].append({"at": e["at"], **data})
            cid = str(data.get("client") or "")
            if cid:
                st.clients.setdefault(
                    cid,
                    {
                        "id": cid,
                        "name": None,
                        "channel": data.get("channel"),
                        "messages": [],
                        "paid": [],
                    },
                )["paid"].append(data.get("product"))
        elif kind == "expense":
            st.money["expenses"] += _money(data.get("amount"))
        elif kind == "channel.start":
            st.channels[data["name"]] = {"enabled": True, "since": e["at"]}
        elif kind == "channel.stop":
            st.channels[data["name"]] = {
                "enabled": False,
                "since": e["at"],
                "reason": data.get("reason"),
            }
        elif kind == "channel.cursor":
            st.channels.setdefault(data["name"], {"enabled": False})["cursor"] = (
                data.get("cursor")
            )
        elif kind == "cycle.start":
            st.cycle.update(running=True, since=e["at"], authority=data.get("command"))
        elif kind == "cycle.stop":
            st.cycle.update(running=False, stopped=e["at"], reason=data.get("reason"))
        elif kind == "cycle.tick":
            st.cycle["steps"] += 1
            st.cycle["last_tick"] = e["at"]
            st.cycle["last_role"] = data.get("role")
            if data.get("role") == "founder":
                st.last_founder = e["at"]
        elif kind == "model.switch":
            st.cycle["model"] = data.get("to")
        elif kind == "staff.file":
            role = str(data.get("role") or e.get("actor") or "")
            if role:
                previous = st.staff.get(role)
                st.staff[role] = {
                    "role": role,
                    "seq": e["seq"],
                    "at": e["at"],
                    "cv": data.get("cv", ""),
                    "mood": data.get("mood", ""),
                    "readiness": data.get("readiness", ""),
                    "notes": data.get("notes", ""),
                    "path": data.get("path", ""),
                    "versions": (previous["versions"] + 1) if previous else 1,
                }
        elif kind == "staff.hire":
            name = str(data.get("role") or "")
            if name:
                earlier = st.hired.get(name)
                st.hired[name] = {
                    "role": name,
                    "title": data.get("title", ""),
                    "instructions": data.get("instructions", ""),
                    "avatar": data.get("avatar", ""),
                    "why": data.get("why", ""),
                    "path": data.get("path", ""),
                    "by": e.get("actor"),
                    "seq": e["seq"],
                    "at": e["at"],
                    "versions": (earlier["versions"] + 1) if earlier else 1,
                }
        elif kind == "note":
            st.notes.append(
                {"seq": e["seq"], "at": e["at"], "actor": e.get("actor"), **data}
            )
            if data.get("office") and data.get("conversation"):
                st.offices[str(data["conversation"])] = data["office"]
                st.office_of[data["office"]] = str(data["conversation"])
        elif kind == "backup":
            st.last_backup = {"at": e["at"], **data}
        elif kind == "invoice":
            st.invoices.append(
                {
                    "seq": e["seq"],
                    "at": e["at"],
                    "client": str(data.get("client") or ""),
                    "channel": data.get("channel"),
                    "offer": e.get("offer"),
                    "product": e.get("product"),
                    "amount": _money(data.get("amount")),
                    "currency": data.get("currency", "RUB"),
                    "text": data.get("text", ""),
                }
            )
        # A site order and its signals, read beside the client.message branch above
        # (which stays as it is for every channel).
        if (
            kind == "client.message"
            and data.get("channel") == "site"
            and data.get("signal")
        ):
            cid = str(data.get("client"))
            if data["signal"] == "order" and e.get("offer"):
                st.orders.setdefault(
                    cid,
                    {
                        "client": cid,
                        "offer": e.get("offer"),
                        "product": e.get("product"),
                        "project": e.get("project"),
                        "at": e["at"],
                        "amount": _money(data.get("amount")),
                        "currency": data.get("currency", "RUB"),
                        "contact": str(data.get("contact") or ""),
                        "key": str(data.get("key") or ""),
                        "signals": {},  # signal -> when it was first sent
                    },
                )
            elif cid in st.orders:
                st.orders[cid]["signals"].setdefault(str(data["signal"]), e["at"])
    return st


_LEDGER: Ledger | None = None
_LEDGER_LOCK = threading.Lock()


def ledger() -> Ledger:
    global _LEDGER
    with _LEDGER_LOCK:
        if _LEDGER is None:
            _LEDGER = Ledger(state_dir() / "corporation" / "ledger.jsonl")
        return _LEDGER


def state() -> State:
    return build_state(ledger().entries())
