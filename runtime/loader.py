"""Load a deliberately small, branch-safe runtime extension contract."""

from __future__ import annotations

import ast
import builtins
import hashlib
import importlib.machinery
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .dispatcher import RuntimeDispatcher


class RuntimeLoadError(ValueError):
    """Raised when revision code does not satisfy the safe loading contract."""


@dataclass(frozen=True)
class RuntimeHostAPI:
    """Stable capability passed to revision code instead of imported host modules."""

    runtime_id: str
    _dispatch_operation: Callable[[str, Mapping[str, Any] | None], Any]

    def dispatch(self, operation: str, payload: Mapping[str, Any] | None = None) -> Any:
        return self._dispatch_operation(operation, payload)


_SAFE_BUILTIN_NAMES = (
    "__build_class__",
    "abs",
    "all",
    "any",
    "bool",
    "bytes",
    "dict",
    "enumerate",
    "Exception",
    "float",
    "int",
    "isinstance",
    "issubclass",
    "len",
    "list",
    "max",
    "min",
    "object",
    "range",
    "reversed",
    "set",
    "sorted",
    "str",
    "sum",
    "tuple",
    "ValueError",
    "zip",
)
_SAFE_BUILTINS = MappingProxyType({name: getattr(builtins, name) for name in _SAFE_BUILTIN_NAMES})


class RuntimeLoader:
    """Load one revision entry point without touching Python import globals.

    The initial contract is intentionally single-file: ``alice_runtime.py`` may
    not import modules and must expose ``create_runtime(host)``.  Its globals
    live in a private dictionary which is not inserted into ``sys.modules``.
    This is collision-safe, but is not a security sandbox for untrusted Python.
    """

    ENTRYPOINT = "alice_runtime.py"

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
        if path.is_symlink():
            raise RuntimeLoadError("runtime entry point must not be a symbolic link")
        if not path.exists():
            return False
        try:
            resolved_path = path.resolve(strict=True)
            resolved_path.relative_to(self.source_root)
        except (OSError, ValueError) as exc:
            raise RuntimeLoadError("runtime entry point must be inside source_root") from exc
        if not resolved_path.is_file():
            raise RuntimeLoadError("runtime entry point must be a regular file")
        loader = importlib.machinery.SourceFileLoader(
            f"_alice_revision_{self.revision}_{self.runtime_id}", str(resolved_path)
        )
        source = loader.get_source(loader.name)
        if source is None:
            raise RuntimeLoadError("runtime entry point could not be read")
        self._validate(source, str(resolved_path))
        namespace: dict[str, Any] = {
            "__builtins__": _SAFE_BUILTINS,
            "__file__": str(resolved_path),
            "__name__": loader.name,
            "__package__": "",
        }
        exec(compile(source, str(resolved_path), "exec"), namespace)
        factory = namespace.get("create_runtime")
        if not callable(factory):
            raise RuntimeLoadError("alice_runtime.py must define create_runtime(host)")
        application = factory(
            RuntimeHostAPI(
                self.runtime_id,
                lambda operation, payload=None: self._dispatcher.dispatch(
                    self.runtime_id, operation, payload
                ),
            )
        )
        if not callable(getattr(application, "invoke", None)):
            raise RuntimeLoadError("runtime application must define invoke(operation, payload)")
        self._application = application
        self._digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        return True

    def invoke(self, operation: str, payload: Mapping[str, Any] | None = None) -> Any:
        if self._application is None:
            raise RuntimeLoadError("revision runtime is not loaded")
        return self._application.invoke(operation, MappingProxyType(dict(payload or {})))

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
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"__import__", "compile", "eval", "exec"}:
                    raise RuntimeLoadError(f"revision call to {node.func.id}() is not supported")
