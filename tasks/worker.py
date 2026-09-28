"""Task worker: claims queued jobs and runs their registered handlers.

Run it as its own process (``python -m tasks``), for example as a Cloud.ru
Container Apps Job with ``--drain`` so it exits once the queue is empty. With
``ALICE_TASK_WORKER=inline`` (the default, for local and Termux use) the web
process starts one background worker thread on the first enqueue instead.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import socket
import threading
import time
import uuid
from typing import Any, Optional

from tasks.queue import TaskQueue, get_task_queue
from tasks.registry import TaskContext, get_task_handler

logger = logging.getLogger(__name__)

# Modules that register task handlers with @register_task.
TASK_MODULES = ("voice_routes",)
PURGE_INTERVAL_SECONDS = 60.0

_INLINE_LOCK = threading.Lock()
_INLINE_WAKE = threading.Event()
_INLINE_THREAD: Optional[threading.Thread] = None


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, "").strip() or default)


def worker_mode() -> str:
    return os.getenv("ALICE_TASK_WORKER", "inline").strip().lower() or "inline"


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def load_task_modules() -> None:
    for module in TASK_MODULES:
        importlib.import_module(module)


def run_one(queue: TaskQueue, worker_id: str, lease_seconds: float) -> bool:
    """Claim and run one job. Returns False when nothing was claimable."""
    job = queue.claim(worker_id, lease_seconds)
    if job is None:
        return False
    context = TaskContext(job_id=job.id, kind=job.kind, payload=job.payload, queue=queue)
    try:
        result = get_task_handler(job.kind)(context)
    except Exception as exc:
        logger.exception("Task %s (%s) failed", job.id, job.kind)
        queue.fail(job, str(exc)[:500])
    else:
        queue.complete(job, result)
    return True


def run_worker(
    *,
    queue: Optional[TaskQueue] = None,
    drain: bool = False,
    poll_interval: Optional[float] = None,
    lease_seconds: Optional[float] = None,
    stop: Optional[threading.Event] = None,
    worker_id: Optional[str] = None,
) -> int:
    """Process jobs until ``stop`` is set, or until the queue is empty with ``drain``."""
    queue = queue or get_task_queue()
    worker_id = worker_id or default_worker_id()
    poll = poll_interval if poll_interval is not None else _env_float("ALICE_TASK_POLL", 1.0)
    lease = lease_seconds or _env_float("ALICE_TASK_LEASE_SECONDS", 900.0)
    retention = _env_float("ALICE_TASK_RETENTION_SECONDS", 3600.0)
    stop = stop or threading.Event()
    processed = 0
    last_purge = 0.0
    while not stop.is_set():
        if run_one(queue, worker_id, lease):
            processed += 1
            continue
        if time.monotonic() - last_purge >= PURGE_INTERVAL_SECONDS:
            queue.purge(retention)
            last_purge = time.monotonic()
        if drain:
            break
        stop.wait(poll)
    return processed


def enqueue(kind: str, payload: dict[str, Any], *, job_id: Optional[str] = None) -> str:
    """Persist a job; in inline mode also wake the in-process worker."""
    job_id = get_task_queue().enqueue(kind, payload, job_id=job_id)
    if worker_mode() == "inline":
        _kick_inline_worker()
    return job_id


def _kick_inline_worker() -> None:
    global _INLINE_THREAD
    with _INLINE_LOCK:
        if _INLINE_THREAD is None or not _INLINE_THREAD.is_alive():
            _INLINE_THREAD = threading.Thread(
                target=_inline_loop, name="alice-task-worker", daemon=True
            )
            _INLINE_THREAD.start()
    _INLINE_WAKE.set()


def _inline_loop() -> None:
    while True:
        _INLINE_WAKE.wait(timeout=5)
        _INLINE_WAKE.clear()
        try:
            # The mode can change at runtime (tests do); only drain while inline.
            if worker_mode() == "inline":
                run_worker(drain=True)
        except Exception:
            logger.exception("Inline task worker pass failed")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Alice Pro background task worker")
    parser.add_argument(
        "--drain",
        action="store_true",
        help="exit when the queue is empty (for Container Apps Jobs)",
    )
    parser.add_argument("--poll-interval", type=float, default=None)
    parser.add_argument("--lease-seconds", type=float, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    load_task_modules()
    get_task_queue().prepare()
    processed = run_worker(
        drain=args.drain,
        poll_interval=args.poll_interval,
        lease_seconds=args.lease_seconds,
    )
    logger.info("Task worker processed %d job(s)", processed)
    return 0
