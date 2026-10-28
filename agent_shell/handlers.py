"""Built-in task kinds. Each handler is read-only unless its docstring says otherwise."""

from __future__ import annotations

import re

from agent_shell.host import bound
from agent_shell.runner import TaskFailed

MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
MAX_TITLE = 200
MAX_BODY = 8000


def default_handlers(check_run=None, run_issue=None):
    def logos_task(payload):
        """Runs Logos headlessly on a task text with its sandboxed filesystem tools.

        Logos never commits, pushes or runs commands, and stops at approval-gated tools
        instead of running them; this handler never approves anything on its behalf.
        """
        if not isinstance(payload, dict):
            raise TaskFailed("invalid_payload")
        issue = payload.get("issue")
        title = payload.get("title")
        body = payload.get("body")
        model = payload.get("model")
        valid = (
            type(issue) is int
            and issue > 0
            and isinstance(title, str)
            and 1 <= len(title) <= MAX_TITLE
            and isinstance(body, str)
            and len(body) <= MAX_BODY
            and (
                model is None or (isinstance(model, str) and MODEL_RE.fullmatch(model))
            )
        )
        if not valid:
            raise TaskFailed("invalid_payload")
        run = run_issue
        if run is None:
            run = bound.run_issue_task
        # A supplied run_issue gets the default model too, never None.
        model = model or bound.DEFAULT_MODEL
        result = run(issue, title, body, model)
        if result.get("status") == "failed":
            raise TaskFailed("agent_failed", result)
        if result.get("status") == "needs_approval":
            raise TaskFailed("needs_approval", result)
        return result

    return {"logos_task": logos_task}
