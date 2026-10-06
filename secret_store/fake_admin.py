"""Deterministic admin backend for Secret Store contract tests."""

from __future__ import annotations

from secret_store.admin import SecretAdminResult
from secret_store.core import SecretRef, SecretValue


class FakeSecretAdminBackend:
    def __init__(self, provider: str = "fake") -> None:
        self.provider = provider
        self._counter = 0
        self._values: dict[SecretRef, str] = {}

    def create(self, *, purpose: str, value: SecretValue) -> SecretAdminResult:
        self._counter += 1
        ref = SecretRef(
            provider=self.provider,
            secret_id=f"secret-{self._counter}",
            version_id="v1",
            purpose=purpose,
        )
        self._values[ref] = value.reveal()
        return SecretAdminResult(ref=ref, created=True)

    def rotate(self, ref: SecretRef, value: SecretValue) -> SecretAdminResult:
        versions = [
            int(item.version_id.removeprefix("v"))
            for item in self._values
            if item.secret_id == ref.secret_id and item.version_id.startswith("v")
        ]
        version = max(versions, default=0) + 1
        rotated = SecretRef(
            provider=ref.provider,
            secret_id=ref.secret_id,
            version_id=f"v{version}",
            purpose=ref.purpose,
        )
        self._values[rotated] = value.reveal()
        return SecretAdminResult(ref=rotated, created=False)

    def delete(self, ref: SecretRef) -> None:
        for item in tuple(self._values):
            if item.secret_id == ref.secret_id:
                del self._values[item]

    def resolve_for_test(self, ref: SecretRef) -> str | None:
        return self._values.get(ref)


__all__ = ["FakeSecretAdminBackend"]
