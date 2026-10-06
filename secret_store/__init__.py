"""Provider-neutral secret resolution boundary."""

from secret_store.core import (
    SecretErrorCode,
    SecretRef,
    SecretResolutionError,
    SecretResolver,
    SecretValue,
)
from secret_store.fake import FakeSecretResolver

__all__ = [
    "FakeSecretResolver",
    "SecretErrorCode",
    "SecretRef",
    "SecretResolutionError",
    "SecretResolver",
    "SecretValue",
]
