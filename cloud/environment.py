"""Provider-neutral lifecycle contract for Alice sandbox environments.

`environment_manager.py` owns environment metadata and selects one adapter per
environment (`local` or `cloudru` today). Adapters isolate the backend that
actually runs commands: a local git worktree + thread for `local`, a remote
Cloud.ru Container Apps Job / Compute VM for `cloudru`.
"""

from __future__ import annotations

from typing import Any, Protocol


class EnvironmentAdapter(Protocol):  # pragma: no cover - structural typing contract only
    """Lifecycle contract implemented by each environment backend."""

    name: str

    def provision(self) -> dict[str, Any]:
        """Allocate backing resources for a newly created environment.

        Returns adapter-specific fields to persist on the environment record
        (for example a remote resource id). Called once, right after the
        environment row is inserted.
        """
        ...

    def start(self) -> dict[str, Any]:
        """Start the environment so it can accept `execute()` calls."""
        ...

    def execute(self, command: str, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
        """Run one command inside the running environment."""
        ...

    def stop(self) -> None:
        """Stop the environment while keeping its backing resource allocated."""
        ...

    def remove(self) -> None:
        """Permanently delete the environment's backing resource."""
        ...
