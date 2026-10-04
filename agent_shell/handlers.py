"""Built-in task kinds. Each handler is read-only unless its docstring says otherwise."""

from __future__ import annotations


def default_handlers(check_run=None):
    def cloudru_status(payload):
        """Read-only: containers and registries in Cloud.ru (names, statuses, sizes)."""
        run = check_run
        if run is None:
            from scripts.cloudru_check import run
        return run(["containers", "registries"])

    return {"cloudru_status": cloudru_status}
