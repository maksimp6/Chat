"""Typed durable task repository using Logos's Kristall client.

One atomic aggregate keeps task IDs, state and events consistent. Separate
TaskStore objects in the same process share a writer; another process is
refused by the engine's writer lock. Legacy SQL files are never overwritten.
"""

from __future__ import annotations

import builtins
import json
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, TypedDict, cast

from agent_shell.host import bound, get_memory_client

ROLE_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
KIND_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
MAX_TITLE = 200
MAX_PAYLOAD_BYTES = 16 * 1024
_STATE_KEY = "agent-shell/tasks"


class TaskRecord(TypedDict):
    id: int
    role: str
    title: str
    kind: str
    payload: Any
    needs_approval: bool
    status: str
    result: Any
    error: str | None
    created_at: float
    updated_at: float


class TaskEvent(TypedDict):
    stage: str
    at: float


class TaskState(TypedDict):
    schema: int
    next_id: int
    tasks: dict[str, TaskRecord]
    events: dict[str, list[TaskEvent]]


def _state(value: Any) -> TaskState:
    if value is None:
        return {"schema": 1, "next_id": 1, "tasks": {}, "events": {}}
    if (
        not isinstance(value, dict)
        or value.get("schema") != 1
        or type(value.get("next_id")) is not int
        or value["next_id"] < 1
        or not isinstance(value.get("tasks"), dict)
        or not isinstance(value.get("events"), dict)
    ):
        raise bound.KristallError("invalid task repository state")
    for key, task in value["tasks"].items():
        if (
            not isinstance(task, dict)
            or type(task.get("id")) is not int
            or str(task["id"]) != key
            or not 0 < task["id"] < value["next_id"]
            or task.get("status")
            not in {"blocked", "queued", "running", "done", "failed"}
            or not isinstance(task.get("role"), str)
            or not ROLE_RE.fullmatch(task["role"])
            or not isinstance(task.get("kind"), str)
            or not KIND_RE.fullmatch(task["kind"])
            or not isinstance(task.get("title"), str)
            or not 1 <= len(task["title"]) <= MAX_TITLE
            or type(task.get("needs_approval")) is not bool
            or not {"payload", "result", "error", "created_at", "updated_at"}
            <= task.keys()
        ):
            raise bound.KristallError("invalid task record")
    if set(value["events"]) != set(value["tasks"]):
        raise bound.KristallError("invalid task event ownership")
    for events in value["events"].values():
        if not isinstance(events, list) or any(
            not isinstance(event, dict)
            or not isinstance(event.get("stage"), str)
            or type(event.get("at")) not in (int, float)
            for event in events
        ):
            raise bound.KristallError("invalid task events")
    return cast(TaskState, value)


def _event(state: TaskState, task_id: int, stage: str) -> None:
    state["events"].setdefault(str(task_id), []).append(
        {"stage": stage, "at": time.time()}
    )


class TaskStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(Path(path))
        self._db = get_memory_client(path)

    def add(
        self,
        *,
        role: str,
        title: str,
        kind: str,
        payload: Any = None,
        needs_approval: bool = False,
    ) -> int:
        if not isinstance(role, str) or not ROLE_RE.fullmatch(role):
            raise ValueError("invalid role")
        if not isinstance(kind, str) or not KIND_RE.fullmatch(kind):
            raise ValueError("invalid kind")
        if not isinstance(title, str) or not 1 <= len(title) <= MAX_TITLE:
            raise ValueError("invalid title")
        # Only the default None becomes {}; any other value is stored as given.
        body = json.dumps(
            {} if payload is None else payload, sort_keys=True, allow_nan=False
        )
        if len(body.encode()) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload too large")
        now = time.time()
        with self._db.transaction() as tx:
            state = _state(tx.get(_STATE_KEY))
            task_id = state["next_id"]
            state["next_id"] += 1
            state["tasks"][str(task_id)] = {
                "id": task_id,
                "role": role,
                "title": title,
                "kind": kind,
                "payload": json.loads(body),
                "needs_approval": bool(needs_approval),
                "status": "blocked" if needs_approval else "queued",
                "result": None,
                "error": None,
                "created_at": now,
                "updated_at": now,
            }
            _event(state, task_id, "queued")
            if needs_approval:
                _event(state, task_id, "blocked")
            tx.set(_STATE_KEY, state)
        return task_id

    def get(self, task_id: int) -> TaskRecord | None:
        return _state(self._db.get(_STATE_KEY))["tasks"].get(str(task_id))

    def list(self) -> builtins.list[TaskRecord]:
        return sorted(
            _state(self._db.get(_STATE_KEY))["tasks"].values(),
            key=lambda task: task["id"],
        )

    def events(self, task_id: int) -> builtins.list[TaskEvent]:
        return _state(self._db.get(_STATE_KEY))["events"].get(str(task_id), [])

    def approve(self, task_id: int) -> None:
        with self._db.transaction() as tx:
            state = _state(tx.get(_STATE_KEY))
            task = state["tasks"].get(str(task_id))
            if task is None or task["status"] != "blocked":
                raise ValueError("task is not waiting for approval")
            task["status"] = "queued"
            task["updated_at"] = time.time()
            _event(state, task_id, "approved")
            tx.set(_STATE_KEY, state)

    def claim_next(self) -> TaskRecord | None:
        claimed = None
        with self._db.transaction() as tx:
            state = _state(tx.get(_STATE_KEY))
            queued = sorted(
                (
                    task
                    for task in state["tasks"].values()
                    if task["status"] == "queued"
                ),
                key=lambda task: task["id"],
            )
            if queued:
                task = queued[0]
                task["status"] = "running"
                task["updated_at"] = time.time()
                _event(state, task["id"], "started")
                tx.set(_STATE_KEY, state)
                claimed = deepcopy(task)
        return claimed

    def finish(self, task_id: int, result: Any) -> None:
        self._finish(
            task_id,
            "done",
            result=json.loads(json.dumps(result, allow_nan=False)),
            stage="finished",
        )

    def fail(self, task_id: int, code: str, result: Any = None) -> None:
        self._finish(
            task_id,
            "failed",
            error=code,
            result=json.loads(json.dumps(result, allow_nan=False)),
            stage="failed",
        )

    def _finish(
        self,
        task_id: int,
        status: str,
        *,
        result: Any = None,
        error: str | None = None,
        stage: str,
    ) -> None:
        with self._db.transaction() as tx:
            state = _state(tx.get(_STATE_KEY))
            task = state["tasks"].get(str(task_id))
            if task is None:
                raise ValueError("task not found")
            # Only a running task gets its outcome: a done task never becomes failed, and a
            # task failed as interrupted is not finished again.
            if task["status"] != "running":
                raise ValueError("task is not running")
            task["status"] = status
            task["result"] = result
            task["error"] = error
            task["updated_at"] = time.time()
            _event(state, task_id, stage)
            tx.set(_STATE_KEY, state)

    def recover_interrupted(self) -> builtins.list[int]:
        """Fail running tasks atomically; never silently repeat external work."""
        with self._db.transaction() as tx:
            state = _state(tx.get(_STATE_KEY))
            ids = []
            for task in sorted(state["tasks"].values(), key=lambda item: item["id"]):
                if task["status"] == "running":
                    ids.append(task["id"])
                    task["status"] = "failed"
                    task["error"] = "interrupted"
                    task["updated_at"] = time.time()
                    _event(state, task["id"], "interrupted")
            if ids:
                tx.set(_STATE_KEY, state)
        return ids
