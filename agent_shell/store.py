"""SQLite task store. Every method opens its own connection, so runners in separate
threads or processes can share one file; a task is claimed atomically."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
import time

ROLE_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
KIND_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
MAX_TITLE = 200
MAX_PAYLOAD_BYTES = 16 * 1024
SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  role TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL,
  payload TEXT NOT NULL, needs_approval INTEGER NOT NULL,
  status TEXT NOT NULL, result TEXT, error TEXT,
  created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  stage TEXT NOT NULL, at REAL NOT NULL
);
"""


class TaskStore:
    def __init__(self, path):
        self.path = str(Path(path))
        with self._connect() as db:
            db.executescript(SCHEMA)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _task(row):
        if row is None:
            return None
        task = dict(row)
        task["payload"] = json.loads(task["payload"])
        task["result"] = json.loads(task["result"]) if task["result"] is not None else None
        task["needs_approval"] = bool(task["needs_approval"])
        return task

    @staticmethod
    def _event(db, task_id, stage):
        db.execute(
            "INSERT INTO events (task_id, stage, at) VALUES (?, ?, ?)",
            (task_id, stage, time.time()),
        )

    def add(self, *, role, title, kind, payload=None, needs_approval=False):
        if not isinstance(role, str) or not ROLE_RE.fullmatch(role):
            raise ValueError("invalid role")
        if not isinstance(kind, str) or not KIND_RE.fullmatch(kind):
            raise ValueError("invalid kind")
        if not isinstance(title, str) or not 1 <= len(title) <= MAX_TITLE:
            raise ValueError("invalid title")
        body = json.dumps(payload or {}, sort_keys=True)
        if len(body.encode()) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload too large")
        now = time.time()
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO tasks (role, title, kind, payload, needs_approval, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    role,
                    title,
                    kind,
                    body,
                    int(bool(needs_approval)),
                    "blocked" if needs_approval else "queued",
                    now,
                    now,
                ),
            )
            task_id = cursor.lastrowid
            self._event(db, task_id, "queued")
            if needs_approval:
                self._event(db, task_id, "blocked")
        return task_id

    def get(self, task_id):
        with self._connect() as db:
            return self._task(db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())

    def list(self):
        with self._connect() as db:
            return [self._task(row) for row in db.execute("SELECT * FROM tasks ORDER BY id")]

    def events(self, task_id):
        with self._connect() as db:
            rows = db.execute(
                "SELECT stage, at FROM events WHERE task_id = ? ORDER BY id", (task_id,)
            )
            return [dict(row) for row in rows]

    def approve(self, task_id):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE tasks SET status = 'queued', updated_at = ? WHERE id = ? AND status = 'blocked'",
                (time.time(), task_id),
            ).rowcount
            if changed != 1:
                db.execute("ROLLBACK")
                raise ValueError("task is not waiting for approval")
            self._event(db, task_id, "approved")
            db.execute("COMMIT")

    def claim_next(self):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id FROM tasks WHERE status = 'queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                db.execute("ROLLBACK")
                return None
            db.execute(
                "UPDATE tasks SET status = 'running', updated_at = ? WHERE id = ?",
                (time.time(), row["id"]),
            )
            self._event(db, row["id"], "started")
            db.execute("COMMIT")
            return self._task(
                db.execute("SELECT * FROM tasks WHERE id = ?", (row["id"],)).fetchone()
            )

    def finish(self, task_id, result):
        self._close(task_id, "done", result=json.dumps(result), stage="finished")

    def fail(self, task_id, code):
        self._close(task_id, "failed", error=code, stage="failed")

    def _close(self, task_id, status, *, result=None, error=None, stage):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE tasks SET status = ?, result = ?, error = ?, updated_at = ? WHERE id = ?",
                (status, result, error, time.time(), task_id),
            )
            self._event(db, task_id, stage)
            db.execute("COMMIT")

    def recover_interrupted(self):
        """A task still 'running' after a crash is failed, never silently re-run."""
        with self._connect() as db:
            ids = [row["id"] for row in db.execute("SELECT id FROM tasks WHERE status = 'running'")]
        for task_id in ids:
            self._close(task_id, "failed", error="interrupted", stage="interrupted")
        return ids
