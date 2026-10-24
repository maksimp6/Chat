"""Deterministic in-memory resolver for tests."""

from __future__ import annotations

from collections.abc import Mapping

from secret_store.core import (
    SecretErrorCode,
    SecretRef,
    SecretResolutionError,
    SecretValue,
)


class FakeSecretResolver:
    def __init__(
        self,
        values: Mapping[SecretRef, str] | None = None,
        *,
        active: Mapping[SecretRef, bool] | None = None,
        available: bool = True,
    ) -> None:
        self._values = dict(values or {})
        self._active = dict(active or {})
        self.available = available

    def resolve(self, ref: SecretRef) -> SecretValue:
        if not self.available:
            raise SecretResolutionError(SecretErrorCode.UNAVAILABLE, ref)
        if ref not in self._values:
            raise SecretResolutionError(SecretErrorCode.NOT_FOUND, ref)
        if not self._active.get(ref, True):
            raise SecretResolutionError(SecretErrorCode.VERSION_INACTIVE, ref)
        return SecretValue(self._values[ref])

    def put(self, ref: SecretRef, value: str, *, active: bool = True) -> None:
        self._values[ref] = value
        self._active[ref] = active


__all__ = ["FakeSecretResolver"]
