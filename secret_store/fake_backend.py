"""Deterministic in-memory SecretBackend for unit tests.

Callers register ``(secret_id, version_id) → plaintext`` mappings up front.
A missing entry raises ``SecretResolverError(NOT_FOUND)``. A version can be
explicitly revoked to simulate ``SecretResolverError(VERSION_DISABLED)``. No
network or process-global state is touched; each instance is fully isolated.

The backend registers resolved values with the current trace exactly as a
production backend must, so canary-redaction tests work without mocking.
"""

from __future__ import annotations

from secret_store.contract import (
    NOT_FOUND,
    VERSION_DISABLED,
    SecretReference,
    SecretResolverError,
)
from trace_manager import get_current_trace


class FakeSecretBackend:
    """Deterministic in-memory SecretBackend for unit tests.

    Attributes
    ----------
    calls:
        Ordered list of every ``SecretReference`` passed to ``resolve``.
    """

    def __init__(
        self,
        values: dict[tuple[str, str], str] | None = None,
        revoked_versions: set[tuple[str, str]] | None = None,
    ) -> None:
        self._values: dict[tuple[str, str], str] = dict(values or {})
        self._revoked: set[tuple[str, str]] = set(revoked_versions or set())
        self.calls: list[SecretReference] = []

    def register(self, secret_id: str, version_id: str, value: str) -> None:
        """Add or update a value entry."""
        self._values[(secret_id, version_id)] = value

    def revoke(self, secret_id: str, version_id: str) -> None:
        """Mark a version as disabled (simulates Cloud.ru version_disabled)."""
        self._revoked.add((secret_id, version_id))

    def resolve(self, reference: SecretReference) -> str:
        self.calls.append(reference)
        key = (reference.secret_id, reference.version_id)
        if key in self._revoked:
            raise SecretResolverError(
                code=VERSION_DISABLED,
                message=f"version '{reference.version_id}' is disabled",
                reference=reference,
            )
        if key not in self._values:
            raise SecretResolverError(
                code=NOT_FOUND,
                message=f"no secret for '{reference.secret_id}:{reference.version_id}'",
                reference=reference,
            )
        value = self._values[key]
        trace = get_current_trace()
        if trace is not None:
            trace.register_sensitive_value(value)
        return value


__all__ = ["FakeSecretBackend"]
