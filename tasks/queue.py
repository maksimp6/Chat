"""Durable job queue backends: SQL (SQLite/PostgreSQL), Redis and in-memory.

Every backend has the same contract:

- ``enqueue`` stores a job before returning, so it survives a web restart. An
  explicit ``job_id`` that already exists raises ``DuplicateJobError``.
- ``claim`` leases one job to a worker. A job whose lease expired (the worker
  died) is handed out again until ``max_attempts`` is spent, then marked failed.
- ``complete``/``fail`` only succeed for the lease holder.
- ``append_event``/``events`` keep an ordered progress log per job, which lets
  the web process stream progress that another process produces.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

import db
from db_backend import postgres_url_from_env

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
LEASE_EXPIRED_ERROR = "worker lease expired too many times"


class DuplicateJobError(ValueError):
    """A job with this id already exists; it is never enqueued twice."""


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Job:
    id: str
    kind: str
    payload: dict[str, Any]
    status: str
    attempts: int = 0
    max_attempts: int = 3
    result: Any = None
    error: Optional[str] = None
    lease_token: Optional[str] = None
    created_at: int = 0
    updated_at: int = 0


class TaskQueue:
    """Interface shared by all queue backends."""

    def prepare(self) -> None:
        """Create storage the backend needs. Called once at process startup."""

    def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        job_id: Optional[str] = None,
        max_attempts: int = 3,
    ) -> str:
        raise NotImplementedError

    def claim(self, worker_id: str, lease_seconds: float) -> Optional[Job]:
        raise NotImplementedError

    def complete(self, job: Job, result: Any = None) -> bool:
        raise NotImplementedError

    def fail(self, job: Job, error: str) -> bool:
        raise NotImplementedError

    def get(self, job_id: str) -> Optional[Job]:
        raise NotImplementedError

    def append_event(self, job_id: str, event: dict[str, Any]) -> None:
        raise NotImplementedError

    def events(self, job_id: str, after: int = 0) -> list[tuple[int, dict[str, Any]]]:
        raise NotImplementedError

    def purge(self, older_than_seconds: float) -> int:
        raise NotImplementedError


def _token(worker_id: str) -> str:
    return f"{worker_id}:{uuid.uuid4().hex}"


class MemoryTaskQueue(TaskQueue):
    """Process-local queue for ALICE_DB_BACKEND=memory and tests. Not durable."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._lease_until: dict[str, int] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}

    def enqueue(self, kind, payload, *, job_id=None, max_attempts=3):
        now = _now_ms()
        job = Job(
            id=job_id or uuid.uuid4().hex,
            kind=kind,
            payload=json.loads(json.dumps(payload)),
            status=QUEUED,
            max_attempts=max_attempts,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            if job.id in self._jobs:
                raise DuplicateJobError(job.id)
            self._jobs[job.id] = job
            self._events.setdefault(job.id, [])
        return job.id

    def claim(self, worker_id, lease_seconds):
        now = _now_ms()
        with self._lock:
            for job in sorted(self._jobs.values(), key=lambda item: item.created_at):
                expired = job.status == RUNNING and self._lease_until.get(job.id, 0) < now
                if expired and job.attempts >= job.max_attempts:
                    job.status, job.error, job.updated_at = FAILED, LEASE_EXPIRED_ERROR, now
                    continue
                if job.status == QUEUED or expired:
                    job.status = RUNNING
                    job.attempts += 1
                    job.lease_token = _token(worker_id)
                    job.updated_at = now
                    self._lease_until[job.id] = now + int(lease_seconds * 1000)
                    return Job(**job.__dict__)
        return None

    def _finish(self, job: Job, status: str, result: Any, error: Optional[str]) -> bool:
        with self._lock:
            stored = self._jobs.get(job.id)
            if stored is None or stored.status != RUNNING or stored.lease_token != job.lease_token:
                return False
            stored.status, stored.result, stored.error = status, result, error
            stored.updated_at = _now_ms()
            return True

    def complete(self, job, result=None):
        return self._finish(job, DONE, result, None)

    def fail(self, job, error):
        return self._finish(job, FAILED, None, error)

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return Job(**job.__dict__) if job else None

    def append_event(self, job_id, event):
        with self._lock:
            self._events.setdefault(job_id, []).append(dict(event))

    def events(self, job_id, after=0):
        with self._lock:
            items = list(self._events.get(job_id, ()))
        return [(seq, event) for seq, event in enumerate(items, start=1) if seq > after]

    def purge(self, older_than_seconds):
        cutoff = _now_ms() - int(older_than_seconds * 1000)
        with self._lock:
            stale = [
                job_id
                for job_id, job in self._jobs.items()
                if job.status in (DONE, FAILED) and job.updated_at < cutoff
            ]
            for job_id in stale:
                self._jobs.pop(job_id, None)
                self._events.pop(job_id, None)
                self._lease_until.pop(job_id, None)
        return len(stale)


_SQL_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS task_jobs (
        job_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,
        lease_owner TEXT,
        lease_until BIGINT,
        result_json TEXT,
        error TEXT,
        created_at BIGINT NOT NULL,
        updated_at BIGINT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_task_jobs_status ON task_jobs(status, created_at)",
    """
    CREATE TABLE IF NOT EXISTS task_events (
        job_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        event_json TEXT NOT NULL,
        created_at BIGINT NOT NULL,
        PRIMARY KEY (job_id, seq)
    )
    """,
)


class SqlTaskQueue(TaskQueue):
    """Queue stored in the application database (SQLite or PostgreSQL)."""

    def __init__(self, connect: Optional[Callable[[], Any]] = None) -> None:
        self._connect = connect or db.get_conn
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    def prepare(self) -> None:
        self._connect_prepared().close()

    def _connect_prepared(self):
        conn = self._connect()
        if not self._schema_ready:
            with self._schema_lock:
                for statement in _SQL_SCHEMA:
                    conn.execute(statement)
                conn.commit()
                self._schema_ready = True
        return conn

    def _conn(self):
        # prepare() runs at web and worker startup; this fallback only matters
        # for a queue used before startup (tests, scripts).
        return self._connect_prepared()

    @staticmethod
    def _row_to_job(row) -> Job:
        result = row["result_json"]
        return Job(
            id=row["job_id"],
            kind=row["kind"],
            payload=json.loads(row["payload_json"]),
            status=row["status"],
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            result=json.loads(result) if result is not None else None,
            error=row["error"],
            lease_token=row["lease_owner"],
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )

    def enqueue(self, kind, payload, *, job_id=None, max_attempts=3):
        job_id = job_id or uuid.uuid4().hex
        now = _now_ms()
        conn = self._conn()
        try:
            try:
                conn.execute(
                    """
                INSERT INTO task_jobs
                (job_id, kind, payload_json, status, attempts, max_attempts,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                """,
                    (job_id, kind, json.dumps(payload), QUEUED, max_attempts, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateJobError(job_id) from exc
            conn.commit()
        finally:
            conn.close()
        return job_id

    def claim(self, worker_id, lease_seconds):
        now = _now_ms()
        token = _token(worker_id)
        conn = self._conn()
        try:
            conn.execute(
                """
                UPDATE task_jobs SET status = ?, error = ?, updated_at = ?
                 WHERE status = ? AND lease_until < ? AND attempts >= max_attempts
                """,
                (FAILED, LEASE_EXPIRED_ERROR, now, RUNNING, now),
            )
            # The status predicate is repeated outside the subquery so that a
            # concurrent claimer on PostgreSQL re-checks it and skips the row.
            conn.execute(
                """
                UPDATE task_jobs
                   SET status = ?, lease_owner = ?, lease_until = ?,
                       attempts = attempts + 1, updated_at = ?
                 WHERE job_id = (
                           SELECT job_id FROM task_jobs
                            WHERE status = ? OR (status = ? AND lease_until < ?)
                            ORDER BY created_at, job_id
                            LIMIT 1
                       )
                   AND (status = ? OR (status = ? AND lease_until < ?))
                """,
                (
                    RUNNING,
                    token,
                    now + int(lease_seconds * 1000),
                    now,
                    QUEUED,
                    RUNNING,
                    now,
                    QUEUED,
                    RUNNING,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM task_jobs WHERE lease_owner = ? AND status = ?",
                (token, RUNNING),
            ).fetchone()
            return self._row_to_job(row) if row else None
        finally:
            conn.close()

    def _finish(self, job: Job, status: str, result: Any, error: Optional[str]) -> bool:
        conn = self._conn()
        try:
            cursor = conn.execute(
                """
                UPDATE task_jobs SET status = ?, result_json = ?, error = ?, updated_at = ?
                 WHERE job_id = ? AND status = ? AND lease_owner = ?
                """,
                (
                    status,
                    json.dumps(result) if result is not None else None,
                    error,
                    _now_ms(),
                    job.id,
                    RUNNING,
                    job.lease_token,
                ),
            )
            conn.commit()
            return cursor.rowcount == 1
        finally:
            conn.close()

    def complete(self, job, result=None):
        return self._finish(job, DONE, result, None)

    def fail(self, job, error):
        return self._finish(job, FAILED, None, error)

    def get(self, job_id):
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM task_jobs WHERE job_id = ?", (job_id,)).fetchone()
            return self._row_to_job(row) if row else None
        finally:
            conn.close()

    def append_event(self, job_id, event):
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO task_events (job_id, seq, event_json, created_at)
                SELECT ?, COALESCE(MAX(seq), 0) + 1, ?, ? FROM task_events WHERE job_id = ?
                """,
                (job_id, json.dumps(event, ensure_ascii=False), _now_ms(), job_id),
            )
            conn.commit()
        finally:
            conn.close()

    def events(self, job_id, after=0):
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT seq, event_json FROM task_events WHERE job_id = ? AND seq > ? ORDER BY seq",
                (job_id, after),
            ).fetchall()
            return [(int(row["seq"]), json.loads(row["event_json"])) for row in rows]
        finally:
            conn.close()

    def purge(self, older_than_seconds):
        cutoff = _now_ms() - int(older_than_seconds * 1000)
        conn = self._conn()
        try:
            cursor = conn.execute(
                "DELETE FROM task_jobs WHERE status IN (?, ?) AND updated_at < ?",
                (DONE, FAILED, cutoff),
            )
            conn.execute(
                "DELETE FROM task_events WHERE job_id NOT IN (SELECT job_id FROM task_jobs)"
            )
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()


_REDIS_CLAIM = """
local expired = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', ARGV[1])
for _, id in ipairs(expired) do
  redis.call('ZREM', KEYS[2], id)
  local key = ARGV[4] .. id
  if tonumber(redis.call('HGET', key, 'attempts')) >= tonumber(redis.call('HGET', key, 'max_attempts')) then
    redis.call('HSET', key, 'status', 'failed', 'error', ARGV[5], 'updated_at', ARGV[1])
    redis.call('ZADD', KEYS[3], ARGV[1], id)
  else
    redis.call('HSET', key, 'status', 'queued')
    redis.call('LPUSH', KEYS[1], id)
  end
end
local id = redis.call('LPOP', KEYS[1])
if not id then return false end
local key = ARGV[4] .. id
redis.call('HINCRBY', key, 'attempts', 1)
redis.call('HSET', key, 'status', 'running', 'lease_owner', ARGV[3], 'updated_at', ARGV[1])
redis.call('ZADD', KEYS[2], ARGV[2], id)
return id
"""

_REDIS_ENQUEUE = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('HSET', KEYS[1], unpack(ARGV, 2))
redis.call('RPUSH', KEYS[2], ARGV[1])
return 1
"""

_REDIS_FINISH = """
if redis.call('HGET', KEYS[1], 'status') ~= 'running'
   or redis.call('HGET', KEYS[1], 'lease_owner') ~= ARGV[1] then
  return 0
end
redis.call('HSET', KEYS[1], 'status', ARGV[2], 'result', ARGV[3], 'error', ARGV[4],
           'updated_at', ARGV[5])
redis.call('ZREM', KEYS[2], ARGV[6])
redis.call('ZADD', KEYS[3], ARGV[5], ARGV[6])
return 1
"""


class RedisTaskQueue(TaskQueue):
    """Queue in Redis (Cloud.ru Managed Redis in the cloud deployment)."""

    def __init__(self, client, prefix: str = "alice:tasks:") -> None:
        self._redis = client
        self._prefix = prefix
        self._ready = f"{prefix}ready"
        self._leases = f"{prefix}leases"
        self._finished = f"{prefix}finished"
        self._enqueue = client.register_script(_REDIS_ENQUEUE)
        self._claim = client.register_script(_REDIS_CLAIM)
        self._finish_script = client.register_script(_REDIS_FINISH)

    def _job_key(self, job_id: str) -> str:
        return f"{self._prefix}job:{job_id}"

    def _events_key(self, job_id: str) -> str:
        return f"{self._prefix}events:{job_id}"

    @staticmethod
    def _text(value) -> str:
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    def enqueue(self, kind, payload, *, job_id=None, max_attempts=3):
        job_id = job_id or uuid.uuid4().hex
        now = _now_ms()
        fields = {
            "kind": kind,
            "payload": json.dumps(payload),
            "status": QUEUED,
            "attempts": 0,
            "max_attempts": max_attempts,
            "result": "",
            "error": "",
            "lease_owner": "",
            "created_at": now,
            "updated_at": now,
        }
        created = self._enqueue(
            keys=[self._job_key(job_id), self._ready],
            args=[job_id, *(item for pair in fields.items() for item in pair)],
        )
        if not created:
            raise DuplicateJobError(job_id)
        return job_id

    def claim(self, worker_id, lease_seconds):
        now = _now_ms()
        token = _token(worker_id)
        job_id = self._claim(
            keys=[self._ready, self._leases, self._finished],
            args=[
                now,
                now + int(lease_seconds * 1000),
                token,
                f"{self._prefix}job:",
                LEASE_EXPIRED_ERROR,
            ],
        )
        if not job_id:
            return None
        return self.get(self._text(job_id))

    def _finish(self, job: Job, status: str, result: Any, error: Optional[str]) -> bool:
        return bool(
            self._finish_script(
                keys=[self._job_key(job.id), self._leases, self._finished],
                args=[
                    job.lease_token,
                    status,
                    json.dumps(result) if result is not None else "",
                    error or "",
                    _now_ms(),
                    job.id,
                ],
            )
        )

    def complete(self, job, result=None):
        return self._finish(job, DONE, result, None)

    def fail(self, job, error):
        return self._finish(job, FAILED, None, error)

    def get(self, job_id):
        raw = self._redis.hgetall(self._job_key(job_id))
        if not raw:
            return None
        data = {self._text(key): self._text(value) for key, value in raw.items()}
        return Job(
            id=job_id,
            kind=data["kind"],
            payload=json.loads(data["payload"]),
            status=data["status"],
            attempts=int(data["attempts"]),
            max_attempts=int(data["max_attempts"]),
            result=json.loads(data["result"]) if data["result"] else None,
            error=data["error"] or None,
            lease_token=data["lease_owner"] or None,
            created_at=int(data["created_at"]),
            updated_at=int(data["updated_at"]),
        )

    def append_event(self, job_id, event):
        self._redis.rpush(self._events_key(job_id), json.dumps(event, ensure_ascii=False))

    def events(self, job_id, after=0):
        items = self._redis.lrange(self._events_key(job_id), after, -1)
        return [
            (seq, json.loads(self._text(item))) for seq, item in enumerate(items, start=after + 1)
        ]

    def purge(self, older_than_seconds):
        cutoff = _now_ms() - int(older_than_seconds * 1000)
        stale = [self._text(item) for item in self._redis.zrangebyscore(self._finished, 0, cutoff)]
        for job_id in stale:
            self._redis.delete(self._job_key(job_id), self._events_key(job_id))
            self._redis.zrem(self._finished, job_id)
        return len(stale)


_QUEUES: dict[tuple, TaskQueue] = {}
_QUEUES_LOCK = threading.Lock()


def _queue_backend() -> tuple[str, str]:
    backend = os.getenv("ALICE_TASK_QUEUE", "").strip().lower()
    redis_url = os.getenv("ALICE_REDIS_URL", "").strip()
    if not backend:
        if redis_url:
            backend = "redis"
        elif db.is_memory_configured():
            backend = "memory"
        else:
            backend = "sql"
    return backend, redis_url


def _create_queue(backend: str, redis_url: str) -> TaskQueue:
    if backend == "sql":
        return SqlTaskQueue()
    if backend == "memory":
        return MemoryTaskQueue()
    if backend == "redis":
        if not redis_url:
            raise ValueError("ALICE_TASK_QUEUE=redis requires ALICE_REDIS_URL")
        import redis

        return RedisTaskQueue(redis.Redis.from_url(redis_url))
    raise ValueError(f"unknown ALICE_TASK_QUEUE backend: {backend!r}")


def get_task_queue() -> TaskQueue:
    """Return the queue selected by the environment (one instance per config)."""
    backend, redis_url = _queue_backend()
    key = (backend, redis_url, db.DB_PATH, postgres_url_from_env())
    with _QUEUES_LOCK:
        queue = _QUEUES.get(key)
        if queue is None:
            queue = _QUEUES[key] = _create_queue(backend, redis_url)
        return queue


def reset_task_queue() -> None:
    with _QUEUES_LOCK:
        _QUEUES.clear()
