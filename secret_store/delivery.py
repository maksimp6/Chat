"""Capability-only secret delivery for agents.

Agents may request an allowed use of an alias, but this boundary never returns
secret plaintext. Trusted consumers receive SecretValue only inside the call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from secret_store.core import SecretValue
from secret_store.manager import SecretManager


@dataclass(frozen=True, slots=True)
class SecretDeliveryResult:
    alias: str
    purpose: str
    operation: str
    ok: bool = True


SecretConsumer = Callable[[SecretValue], None]


def deliver_secret(
    manager: SecretManager,
    *,
    alias: str,
    purpose: str,
    operation: str,
    consumer: SecretConsumer,
) -> SecretDeliveryResult:
    if operation not in {"browser.fill", "file.mount"}:
        raise ValueError("unsupported secret delivery operation")
    value = manager.use(alias, purpose)
    consumer(value)
    return SecretDeliveryResult(alias=alias, purpose=purpose, operation=operation)


__all__ = ["SecretConsumer", "SecretDeliveryResult", "deliver_secret"]
