"""Provider-neutral secret administration contract.

Admin backends create/rotate/delete secret material and return metadata references
only. Runtime consumers must use SecretResolver instead of this interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from secret_store.core import SecretRef, SecretValue


@dataclass(frozen=True, slots=True)
class SecretAdminResult:
    ref: SecretRef
    created: bool


class SecretAdminBackend(Protocol):
    def create(self, *, purpose: str, value: SecretValue) -> SecretAdminResult: ...

    def rotate(self, ref: SecretRef, value: SecretValue) -> SecretAdminResult: ...

    def delete(self, ref: SecretRef) -> None: ...


__all__ = ["SecretAdminBackend", "SecretAdminResult"]
