"""Test-only line protocol exposing SecretManager use without returning metadata.

The protocol writes only the resolved value to stdout for the direct browser
consumer. It is never a production HTTP/MCP surface.
"""

from __future__ import annotations

import json
import sys

from secret_store.core import SecretRef, SecretResolutionError, SecretValue
from secret_store.fake_admin import FakeSecretAdminBackend
from secret_store.manager import InMemorySecretAliasStore, SecretManager


class _AdminResolver:
    def __init__(self, admin: FakeSecretAdminBackend) -> None:
        self._admin = admin

    def resolve(self, ref: SecretRef) -> SecretValue:
        value = self._admin.resolve_for_test(ref)
        if value is None:
            raise SecretResolutionError("not_found", ref)  # type: ignore[arg-type]
        return SecretValue(value)


def main() -> int:
    admin = FakeSecretAdminBackend()
    manager = SecretManager(InMemorySecretAliasStore(), _AdminResolver(admin), admin)
    manager.create(
        "github",
        SecretValue("canary-cross-runtime-v1"),
        secret_purpose="github",
        allowed_purposes=frozenset({"browser.password"}),
    )
    manager.rotate("github", SecretValue("canary-cross-runtime-v2"))

    request = json.loads(sys.stdin.readline())
    value = manager.use(str(request["alias"]), str(request["purpose"]))
    sys.stdout.write(value.reveal())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
