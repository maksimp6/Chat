"""Storage port for the 3D-printing domain.

The domain code depends only on this protocol. The host (Alice Pro) passes a
concrete store into each operation, so this package never imports Alice
storage or identity modules.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol


class PrintTransaction(Protocol):
    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any) -> None: ...


class PrintStore(Protocol):
    def get(self, key: str) -> Any | None: ...

    def items(self, *, prefix: str) -> dict[str, Any]: ...

    def transaction(self) -> AbstractContextManager[PrintTransaction]: ...
