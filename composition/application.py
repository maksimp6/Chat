"""The web application as a library, for command-line roots that need its state."""

from __future__ import annotations


def initialize() -> None:
    """Import app.py once: environment, log, hosts, storage schema, routes and tools."""
    import app  # noqa: F401
