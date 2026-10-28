# Vendored from core/host_binding.py. Do not edit this copy: change the canonical file
# and run `python3 scripts/check_independence.py --sync-vendored`.
"""Late binding of a unit's host: the ports a composition root provides.

Every unit that needs something from another unit declares it as a field of its
``Host`` (``<unit>/host.py``) and reads it only when a function runs. A composition
root (``composition/wiring.py``) registers a factory with ``provide``; the host is
built on first use, so registering is cheap and import order does not matter.

This file is vendored into each such unit as ``_binding.py`` (pyproject.toml
``[tool.logos.vendored]``), so units share the mechanism without importing each other.
"""

from __future__ import annotations

import importlib
import threading
from collections.abc import Callable
from typing import Any, Generic, TypeVar

H = TypeVar("H")


class HostNotBound(RuntimeError):
    """A unit function ran before a composition root provided the unit's host."""


# The process root used when code imports a unit directly (a library user, a device
# launcher, `python -c ...`) and nothing registered the hosts: importing a unit worked
# on its own before units were wired through ports, and it still does.
DEFAULT_ROOT = "composition.wiring"


def _register_default_root() -> None:
    """Register every host through the default root, outside any binding's lock."""
    try:
        root = importlib.import_module(DEFAULT_ROOT)
    except ModuleNotFoundError as exc:
        # A unit copied on its own has no root; any other missing module is a real error.
        if exc.name is None or not DEFAULT_ROOT.startswith(exc.name):
            raise
        return
    root.register_all()


class Binding(Generic[H]):
    """Holds one unit's host; process-global so worker threads see it too."""

    def __init__(self, unit: str) -> None:
        self._unit = unit
        self._host: H | None = None
        self._factory: Callable[[], H] | None = None
        self._lock = threading.RLock()

    def provide(self, factory: Callable[[], H]) -> None:
        """Register how to build the host; it is built on first use."""
        with self._lock:
            self._factory = factory

    def bind(self, host: H) -> None:
        """Bind a ready host (for roots that build it themselves)."""
        with self._lock:
            self._host = host

    def current(self) -> H:
        host = self._host
        if host is not None:
            return host
        if self._factory is None:
            _register_default_root()
        with self._lock:
            if self._host is None:
                if self._factory is None:
                    raise HostNotBound(
                        f"{self._unit}: no host; a composition root must call "
                        "composition.wiring.register_all() first"
                    )
                self._host = self._factory()
            return self._host

    def names(self) -> Any:
        """An object whose attributes are the host's fields, resolved at use time."""
        binding = self

        class Bound:
            __slots__ = ()

            def __getattr__(self, name: str) -> Any:
                return getattr(binding.current(), name)

        return Bound()
