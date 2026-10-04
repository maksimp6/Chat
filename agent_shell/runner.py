"""Runs one queued task through a handler registry.

Handlers are the only code a task can reach: an unknown kind fails without running
anything. A failure stores a fixed code, never the exception text, so a handler that
touched a secret cannot leak it into the task record or its events.
"""

from __future__ import annotations

import json


def run_next(store, handlers):
    task = store.claim_next()
    if task is None:
        return None
    task_id = task["id"]
    handler = handlers.get(task["kind"])
    if handler is None:
        store.fail(task_id, "unknown_kind")
        return store.get(task_id)
    try:
        result = handler(task["payload"])
    except Exception:  # fixed code only: the message may contain a secret
        store.fail(task_id, "task_failed")
        return store.get(task_id)
    try:
        json.dumps(result)
    except (TypeError, ValueError):
        store.fail(task_id, "invalid_result")
        return store.get(task_id)
    store.finish(task_id, result)
    return store.get(task_id)
