"""Filesystem boundary for importing and temporarily materializing secrets."""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from secret_store.admin import SecretAdminBackend, SecretAdminResult
from secret_store.core import SecretRef, SecretResolver, SecretValue


def import_secret_file(
    path: str | os.PathLike[str],
    *,
    purpose: str,
    admin: SecretAdminBackend,
) -> SecretAdminResult:
    value = Path(path).read_text(encoding="utf-8")
    if not value:
        raise ValueError("secret file must be non-empty")
    return admin.create(purpose=purpose, value=SecretValue(value))


@contextmanager
def materialize_secret_file(
    resolver: SecretResolver,
    ref: SecretRef,
    *,
    directory: str | os.PathLike[str] | None = None,
) -> Iterator[Path]:
    secret = resolver.resolve(ref)
    fd, raw_path = tempfile.mkstemp(prefix="secret-", dir=directory)
    path = Path(raw_path)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as handle:
            handle.write(secret.reveal())
        yield path
    finally:
        path.unlink(missing_ok=True)


__all__ = ["import_secret_file", "materialize_secret_file"]
