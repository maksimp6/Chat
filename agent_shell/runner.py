"""Runs one queued task through a handler registry.

Handlers are the only code a task can reach: an unknown kind fails without running
anything. A failure stores a fixed code, never the exception text, so a handler that
touched a secret cannot leak it into the task record or its events.
"""

from __future__ import annotations

import json

# The only failure codes a handler may report; anything else becomes "task_failed",
# so a handler cannot smuggle text (or a secret) into the task record.
SAFE_CODES = frozenset({"invalid_payload", "agent_failed", "needs_approval"})


class TaskFailed(Exception):
    """Raised by a handler to fail its task with an allow-listed code and a result."""

    def __init__(self, code, result=None):
        super().__init__(code)
        self.code = code
        self.result = result


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
    except TaskFailed as exc:
        known = exc.code in SAFE_CODES
        try:
            json.dumps(exc.result)
        except (TypeError, ValueError):
            exc.result = None
        store.fail(task_id, exc.code if known else "task_failed", exc.result if known else None)
        return store.get(task_id)
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
