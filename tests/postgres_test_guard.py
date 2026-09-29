"""Fail-closed guard for destructive PostgreSQL pytest isolation."""

from __future__ import annotations

import os
from urllib.parse import urlparse


def require_disposable_postgres_target(database_url: str) -> None:
    if os.environ.get("ALICE_PYTEST_POSTGRES_RESET", "").strip() != "1":
        raise RuntimeError(
            "PostgreSQL pytest isolation is destructive; set "
            "ALICE_PYTEST_POSTGRES_RESET=1 only for a disposable test database"
        )

    database_name = urlparse(database_url).path.rsplit("/", 1)[-1].lower()
    if not database_name or not database_name.endswith(("_test", "_ci")):
        raise RuntimeError(
            "Refusing destructive PostgreSQL pytest isolation for non-test database "
            f"{database_name or '<unknown>'!r}"
        )
