"""Load a deliberately small, branch-safe runtime extension contract."""

from __future__ import annotations

import ast
import builtins
import hashlib
import importlib.machinery
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from runtime.dispatcher import RuntimeDispatcher

# Builtins that give revision code the host: files, the terminal, and type introspection.
_DENIED_BUILTINS = frozenset(
    {"breakpoint", "exit", "help", "input", "open", "quit", "type"}
)


class RuntimeLoadError(ValueError):
    """Raised when revision code does not satisfy the safe loading contract."""


@dataclass(frozen=True)
class RuntimeHostAPI:
    """Stable capability passed to revision code instead of imported host modules."""

    runtime_id: str
    dispatcher: RuntimeDispatcher

    def dispatch(self, operation: str, payload: Mapping[str, Any] | None = None) -> Any:
        return self.dispatcher.dispatch(self.runtime_id, operation, payload)


class RuntimeLoader:
    """Load one revision entry point without touching Python import globals.

    The initial contract is intentionally single-file: ``logos_runtime.py`` may
    not import modules and must expose ``create_runtime(host)``.  Its globals
    live in a private dictionary which is not inserted into ``sys.modules``.
    This is collision-safe, but is not a security sandbox for untrusted Python.
    """

    ENTRYPOINT = "logos_runtime.py"

    def __init__(
        self,
        *,
        runtime_id: str,
        revision: str,
        source_root: str | Path,
        dispatcher: RuntimeDispatcher,
    ) -> None:
        self.runtime_id = runtime_id
        self.revision = revision
        self.source_root = Path(source_root).resolve()
        self._dispatcher = dispatcher
        self._application: Any = None
        self._digest: str | None = None

    @property
    def loaded(self) -> bool:
        return self._application is not None

    @property
    def source_digest(self) -> str | None:
        return self._digest

    def load(self) -> bool:
        path = self.source_root / self.ENTRYPOINT
        if not path.is_file():
            return False
        loader = importlib.machinery.SourceFileLoader(
            f"_logos_revision_{self.revision}_{self.runtime_id}", str(path)
        )
        source = loader.get_source(loader.name)
        if source is None:
            raise RuntimeLoadError("runtime entry point could not be read")
        self._validate(source, str(path))
        namespace: dict[str, Any] = {
            "__builtins__": {
                name: value
                for name, value in vars(builtins).items()
                if name not in _DENIED_BUILTINS
            },
            "__file__": str(path),
            "__name__": loader.name,
            "__package__": "",
        }
        exec(compile(source, str(path), "exec"), namespace)
        factory = namespace.get("create_runtime")
        if not callable(factory):
            raise RuntimeLoadError("logos_runtime.py must define create_runtime(host)")
        application = factory(RuntimeHostAPI(self.runtime_id, self._dispatcher))
        if not callable(getattr(application, "invoke", None)):
            raise RuntimeLoadError(
                "runtime application must define invoke(operation, payload)"
            )
        self._application = application
        self._digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        return True

    def invoke(self, operation: str, payload: Mapping[str, Any] | None = None) -> Any:
        if self._application is None:
            raise RuntimeLoadError("revision runtime is not loaded")
        return self._application.invoke(
            operation, MappingProxyType(dict(payload or {}))
        )

    @staticmethod
    def _validate(source: str, filename: str) -> None:
        try:
            tree = ast.parse(source, filename=filename)
        except SyntaxError as exc:
            raise RuntimeLoadError(f"invalid runtime entry point: {exc.msg}") from exc
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                raise RuntimeLoadError(
                    "revision imports are not supported by the single-interpreter runtime contract"
                )
            if isinstance(node, ast.Name) and node.id in {
                "__import__",
                "compile",
                "delattr",
                "eval",
                "exec",
                "getattr",
                "globals",
                "locals",
                "setattr",
                "vars",
            }:
                raise RuntimeLoadError(f"revision use of {node.id} is not supported")
            if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
                raise RuntimeLoadError(
                    "revision access to dunder attributes is not supported"
                )
            # str.format resolves attribute paths such as "{0.__globals__}" at runtime.
            if isinstance(node, ast.Attribute) and node.attr in {
                "format",
                "format_map",
            }:
                raise RuntimeLoadError("revision use of str.format is not supported")
            if isinstance(node, ast.Name) and node.id == "__builtins__":
                raise RuntimeLoadError(
                    "revision access to __builtins__ is not supported"
                )
