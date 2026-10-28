"""The web shell of Logos: the page, health and version endpoints, request policy
(caching, unexpected errors) and storage start-up. ``install(app)`` adds it to a
Flask application; the composition root decides what else the application serves."""

from __future__ import annotations

from typing import Any

__all__ = ["install"]


def __getattr__(name: str) -> Any:
    if name == "install":
        from web_shell.shell import install

        return install
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
