"""Typed secret reference and resolution contracts with redacted values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class SecretErrorCode(StrEnum):
    INVALID_REFERENCE = "invalid_reference"
    NOT_FOUND = "not_found"
    VERSION_INACTIVE = "version_inactive"
    AUTH_FAILED = "auth_failed"
    UNAVAILABLE = "unavailable"
    PROVIDER_ERROR = "provider_error"


@dataclass(frozen=True, slots=True)
class SecretRef:
    provider: str
    secret_id: str
    version_id: str
    purpose: str = ""

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.secret_id.strip() or not self.version_id.strip():
            raise ValueError("provider, secret_id and version_id are required")

    def metadata(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "secret_id": self.secret_id,
            "version_id": self.version_id,
            "purpose": self.purpose,
        }


class SecretValue:
    """Opaque plaintext holder. String/repr/JSON surfaces never reveal the value."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("secret value must be non-empty text")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "<SecretValue redacted>"

    def __str__(self) -> str:
        return "[REDACTED]"

    def __reduce__(self) -> object:
        raise TypeError("SecretValue cannot be serialized")


class SecretResolutionError(Exception):
    def __init__(self, code: SecretErrorCode, ref: SecretRef) -> None:
        self.code = code
        self.ref = ref
        super().__init__(f"secret resolution failed: {code.value}")


class SecretResolver(Protocol):
    def resolve(self, ref: SecretRef) -> SecretValue: ...


__all__ = [
    "SecretErrorCode",
    "SecretRef",
    "SecretResolutionError",
    "SecretResolver",
    "SecretValue",
]
