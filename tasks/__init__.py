"""Durable background tasks executed outside the web request path.

Web handlers enqueue a job and return; a worker process claims it, runs the
registered handler and records progress events and the result. The queue is
durable (SQL or Redis), so a web container can be stopped or scaled to zero
without losing accepted work. See ``docs/runtime/background-tasks.md``.
"""

from tasks.queue import (
    Job,
    MemoryTaskQueue,
    RedisTaskQueue,
    SqlTaskQueue,
    TaskQueue,
    get_task_queue,
    reset_task_queue,
)
from tasks.registry import TaskContext, get_task_handler, register_task
from tasks.worker import enqueue, run_worker

__all__ = [
    "Job",
    "MemoryTaskQueue",
    "RedisTaskQueue",
    "SqlTaskQueue",
    "TaskContext",
    "TaskQueue",
    "enqueue",
    "get_task_handler",
    "get_task_queue",
    "register_task",
    "reset_task_queue",
    "run_worker",
]
