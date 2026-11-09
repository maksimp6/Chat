"""Provider-neutral SecretResolver.

Thin wrapper that accepts any ``SecretBackend`` and enforces the contract:
typed ``SecretReference`` in, plaintext out (or ``SecretResolverError``
raised). No caching, no fallback, no provider-specific logic here.
"""

from __future__ import annotations

from secret_store.contract import SecretBackend, SecretReference


class SecretResolver:
    """Resolve a ``SecretReference`` through a pluggable ``SecretBackend``.

    The resolver intentionally contains no provider logic. Swap the backend
    to switch providers; the contract guarantees identical error semantics
    across all backends.
    """

    def __init__(self, backend: SecretBackend) -> None:
        self._backend = backend

    def resolve(self, reference: SecretReference) -> str:
        """Return the plaintext value for *reference*.

        Raises ``SecretResolverError`` on any failure. The error is safe to
        log and never contains the resolved plaintext.
        """
        return self._backend.resolve(reference)


__all__ = ["SecretResolver"]
