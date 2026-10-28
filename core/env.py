"""The one place that reads or writes the process environment.

Configuration reaches the code through these functions, so there is a single entry point for the
environment. They are thin wrappers over os.environ and cache nothing, so a value set after startup is
seen by the next read. ``load_env_file`` is the one loader; composition roots call it at start-up.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional


def env_get(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name, default)


def env_require(name: str) -> str:
    return os.environ[name]


def env_setdefault(name: str, *default: Any) -> str:
    return os.environ.setdefault(name, *default)


def env_pop(name: str, *default: Any) -> Any:
    return os.environ.pop(name, *default)


def env_set(name: str, value: str) -> None:
    os.environ[name] = value


def env_snapshot() -> dict[str, str]:
    return dict(os.environ)


# Names that only core/.env provided: the process's caller did not set them.
_FILE_ONLY: set[str] = set()


def load_env_file() -> None:
    """Load ``core/.env`` into the environment; variables that are already set win."""
    from dotenv import load_dotenv

    before = set(os.environ)
    load_dotenv(Path(__file__).resolve().with_name(".env"))
    _FILE_ONLY.update(set(os.environ) - before)


def env_file_only(name: str) -> bool:
    """Whether ``name`` came only from core/.env, not from the process's caller."""
    return name in _FILE_ONLY
