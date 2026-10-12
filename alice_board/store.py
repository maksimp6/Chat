"""Private durable Alice Board, backed by the existing append-only FileMemoryDB.

One process owns the board. Each event is one checksummed/fsynced Memory DB put.
No SQL, GitHub API, timers or production cutover.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Self

from agent_memory.file_memory_db import FileMemoryDB, FileMemoryError


class BoardError(ValueError):
    """Invalid request, unknown resource or rejected operation."""


class BoardConflict(BoardError):
    """Idempotency collision or unaccepted task transition."""


class BoardUnavailable(RuntimeError):
    """A second writer or an uncertain durable write prevents operation."""


PREFIX = "alice-board:v1:event:"
CATEGORIES = {"discussion", "shame", "decision"}
STATUSES = {"backlog", "blocked", "ready", "in_progress", "review", "done"}
IDEMPOTENCY = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{7,127}$")


def _required(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise BoardError(f"invalid {name}")
    return value.strip()


def _sha(kind: str, payload: dict[str, Any]) -> str:
    blob = json.dumps(
        [kind, payload], sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class BoardStore:
    """Single-writer event store with immutable posts, traceable tasks and replay."""

    def __init__(self, path: str | Path) -> None:
        requested = Path(path).expanduser()
        if requested.is_symlink() or requested.parent.is_symlink():
            raise BoardUnavailable("board path must not be a symlink")
        self.path = requested.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory = self.path.parent.stat()
        if directory.st_uid != os.getuid() or directory.st_mode & 0o077:
            raise BoardUnavailable("board directory must be owner-private")
        self._pid = os.getpid()
        self._mutex = RLock()
        self._closed = False
        self._poisoned = False
        self._lock_fd = os.open(
            self.path.with_name(self.path.name + ".board.lock"),
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._lock_fd)
            raise BoardUnavailable("board already has a writer") from exc
        try:
            self._db = FileMemoryDB(self.path)
            if self.path.exists() and (
                self.path.stat().st_mode & 0o077 or self.path.stat().st_uid != os.getuid()
            ):
                raise BoardUnavailable("database permissions are too broad")
            entries = self._db.items()
            if any(not key.startswith(PREFIX) for key in entries):
                raise BoardUnavailable("unexpected key in dedicated board database")
            try:
                ordered = sorted(entries.items(), key=lambda row: int(row[0][len(PREFIX) :]))
            except ValueError as exc:
                raise BoardUnavailable("invalid event key") from exc
            self._events: list[dict[str, Any]] = []
            self._idem: dict[str, dict[str, Any]] = {}
            for seq, (key, value) in enumerate(ordered, 1):
                if (
                    key != f"{PREFIX}{seq:09d}"
                    or not isinstance(value, dict)
                    or value.get("seq") != seq
                ):
                    raise BoardUnavailable("event sequence is corrupt")
                if value.get("digest") != _sha(value["kind"], value["payload"]):
                    raise BoardUnavailable("event digest mismatch")
                rid = value["request_id"]
                if rid in self._idem:
                    raise BoardUnavailable("reused idempotency key in history")
                self._events.append(value)
                self._idem[rid] = value
            if self._db.sequence != len(self._events):
                raise BoardUnavailable("Memory DB sequence does not match board history")
            self._project()
            if not any(t["category"] == "shame" for t in self.threads()):
                self.create_thread(
                    "Доска позора",
                    "Подтверждённые ошибки Alice Pro и регрессионные проверки.",
                    "shame",
                    "bootstrap:alice-board:shame:v1",
                    actor="system",
                )
        except BaseException:
            self.close()
            raise

    def _check(self, *, write: bool = False) -> None:
        if self._closed or self._pid != os.getpid():
            raise BoardUnavailable("board handle is closed or belongs to another process")
        if write and self._poisoned:
            raise BoardUnavailable("uncertain write; reopen board after recovery")

    def _project(self) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        threads: dict[str, dict[str, Any]] = {}
        tasks: dict[str, dict[str, Any]] = {}
        try:
            for e in self._events:
                kind, p = e["kind"], e["payload"]
                eid, at = e["id"], e["at"]
                if kind == "thread.created":
                    tid = f"thread-{e['seq']:06d}"
                    threads[tid] = {
                        "id": tid,
                        "title": p["title"],
                        "category": p["category"],
                        "created_at": at,
                        "posts": [{"id": eid, "actor": e["actor"], "body": p["body"], "at": at}],
                    }
                elif kind == "thread.posted":
                    threads[p["thread_id"]]["posts"].append(
                        {"id": eid, "actor": e["actor"], "body": p["body"], "at": at}
                    )
                elif kind == "task.created":
                    tid = f"task-{e['seq']:06d}"
                    tasks[tid] = {
                        "id": tid,
                        "title": p["title"],
                        "depends_on": p["depends_on"],
                        "status": "blocked" if p["depends_on"] else "backlog",
                        "created_at": at,
                        "history": [],
                        "evidence": [],
                    }
                elif kind == "task.status":
                    t = tasks[p["task_id"]]
                    t["status"] = p["status"]
                    t["history"].append(
                        {
                            "status": p["status"],
                            "at": at,
                            "accepted_by_owner": p["accepted_by_owner"],
                        }
                    )
                elif kind == "task.evidence":
                    tasks[p["task_id"]]["evidence"].append(
                        {
                            "id": eid,
                            "summary": p["summary"],
                            "url": p["url"],
                            "verified": False,
                            "at": at,
                        }
                    )
                else:
                    raise BoardUnavailable("unsupported board event")
        except (KeyError, TypeError, ValueError) as exc:
            raise BoardUnavailable("board history cannot be projected") from exc
        return threads, tasks

    def _prior(self, kind: str, payload: dict[str, Any], request_id: str) -> bool:
        previous = self._idem.get(request_id)
        if previous is None:
            return False
        if previous["digest"] != _sha(kind, payload):
            raise BoardConflict("idempotency key was used for a different request")
        return True

    def _append(
        self, kind: str, payload: dict[str, Any], request_id: str, *, actor: str = "owner"
    ) -> dict[str, Any]:
        with self._mutex:
            self._check(write=True)
            if not isinstance(request_id, str) or not IDEMPOTENCY.fullmatch(request_id):
                raise BoardError("invalid idempotency key")
            if self._prior(kind, payload, request_id):
                return deepcopy(self._idem[request_id])
            seq = len(self._events) + 1
            event = {
                "schema": 1,
                "id": f"event-{seq:06d}",
                "seq": seq,
                "kind": kind,
                "payload": payload,
                "actor": actor,
                "request_id": request_id,
                "digest": _sha(kind, payload),
                "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            }
            try:
                confirmed = self._db.put(f"{PREFIX}{seq:09d}", event)
                if confirmed != seq:
                    raise BoardUnavailable("unexpected Memory DB commit sequence")
            except (OSError, FileMemoryError, ValueError, TypeError, BoardUnavailable) as exc:
                self._poisoned = True
                raise BoardUnavailable("uncertain write; recovery required") from exc
            self._events.append(event)
            self._idem[request_id] = event
            return deepcopy(event)

    def threads(self) -> list[dict[str, Any]]:
        with self._mutex:
            self._check()
            threads, _ = self._project()
            return [
                {k: deepcopy(v) for k, v in item.items() if k != "posts"}
                | {"post_count": len(item["posts"])}
                for item in reversed(list(threads.values()))
            ]

    def thread(self, thread_id: str) -> dict[str, Any]:
        with self._mutex:
            self._check()
            threads, _ = self._project()
            if thread_id not in threads:
                raise BoardError("thread not found")
            return deepcopy(threads[thread_id])

    def create_thread(
        self, title: str, body: str, category: str, request_id: str, *, actor: str = "owner"
    ) -> dict[str, Any]:
        title = _required(title, "title", 160)
        body = _required(body, "body", 12000)
        if category not in CATEGORIES:
            raise BoardError("invalid category")
        event = self._append(
            "thread.created",
            {"title": title, "body": body, "category": category},
            request_id,
            actor=actor,
        )
        return self.thread(f"thread-{event['seq']:06d}")

    def post(self, thread_id: str, body: str, request_id: str) -> dict[str, Any]:
        body = _required(body, "body", 12000)
        with self._mutex:
            self.thread(thread_id)
            event = self._append(
                "thread.posted",
                {"thread_id": thread_id, "body": body},
                request_id,
            )
            return {"id": event["id"], "at": event["at"], "body": body}

    def tasks(self) -> list[dict[str, Any]]:
        with self._mutex:
            self._check()
            _, tasks = self._project()
            return [deepcopy(t) for t in reversed(list(tasks.values()))]

    def task(self, task_id: str) -> dict[str, Any]:
        with self._mutex:
            self._check()
            _, tasks = self._project()
            if task_id not in tasks:
                raise BoardError("task not found")
            return deepcopy(tasks[task_id])

    def create_task(self, title: str, depends_on: list[str], request_id: str) -> dict[str, Any]:
        title = _required(title, "title", 160)
        if not isinstance(depends_on, list) or len(depends_on) > 20:
            raise BoardError("invalid dependencies")
        with self._mutex:
            existing = {task["id"] for task in self.tasks()}
            if any(not isinstance(d, str) or d not in existing for d in depends_on) or len(
                set(depends_on)
            ) != len(depends_on):
                raise BoardError("invalid task dependency")
            event = self._append(
                "task.created",
                {"title": title, "depends_on": depends_on},
                request_id,
            )
            return self.task(f"task-{event['seq']:06d}")

    def set_status(
        self, task_id: str, status: str, accepted_by_owner: bool, request_id: str
    ) -> dict[str, Any]:
        if status not in STATUSES or type(accepted_by_owner) is not bool:
            raise BoardError("invalid status")
        payload = {
            "task_id": task_id,
            "status": status,
            "accepted_by_owner": accepted_by_owner,
        }
        transitions = {
            "backlog": {"ready", "blocked"},
            "blocked": {"ready"},
            "ready": {"in_progress", "blocked"},
            "in_progress": {"review", "blocked"},
            "review": {"in_progress", "done", "blocked"},
            "done": set(),
        }
        with self._mutex:
            if self._prior("task.status", payload, request_id):
                return self.task(task_id)
            t = self.task(task_id)
            if status not in transitions[t["status"]]:
                raise BoardConflict("invalid status transition")
            if status != "blocked" and any(
                self.task(d)["status"] != "done" for d in t["depends_on"]
            ):
                raise BoardConflict("dependencies not done")
            if status == "done" and not accepted_by_owner:
                raise BoardConflict("owner acceptance required; this is NOT merge")
            self._append("task.status", payload, request_id)
            return self.task(task_id)

    def add_evidence(self, task_id: str, summary: str, url: str, request_id: str) -> dict[str, Any]:
        summary = _required(summary, "summary", 400)
        if not isinstance(url, str) or len(url) > 1000 or (url and not url.startswith("https://")):
            raise BoardError("invalid evidence URL")
        with self._mutex:
            self.task(task_id)
            self._append(
                "task.evidence",
                {"task_id": task_id, "summary": summary, "url": url},
                request_id,
            )
            return self.task(task_id)

    def healthy(self) -> bool:
        """Return false after an uncertain write instead of reporting fake readiness."""
        with self._mutex:
            self._check()
            return not self._poisoned

    def export(self) -> dict[str, Any]:
        with self._mutex:
            self._check()
            return {
                "schema": 1,
                "sequence": len(self._events),
                "events": deepcopy(self._events),
            }

    def close(self) -> None:
        with self._mutex:
            if self._closed:
                return
            self._closed = True
            if os.getpid() == self._pid:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
