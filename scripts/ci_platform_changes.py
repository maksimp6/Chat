#!/usr/bin/env python3
"""Classify changed paths so independent platforms do not block each other."""

from __future__ import annotations

import json
import sys


def classify(paths: list[str]) -> dict[str, bool]:
    web_prefixes = (
        "static/",
        "templates/",
        "deploy/chrome-worker/",
        "deploy/oauth-idp/",
    )
    web_files = {"app.py", "package.json", "package-lock.json"}
    android = any(path.startswith("android/") for path in paths)
    web = any(path in web_files or path.startswith(web_prefixes) for path in paths)
    infra = any(
        path.startswith(("cloud/", "deploy/", "scripts/", ".github/workflows/", "config/alice/"))
        for path in paths
    )
    backend = any(
        (
            path.endswith(".py")
            and not path.startswith(("cloud/", "scripts/", "deploy/", "android/"))
            and path != "tests/test_ci_platform_changes.py"
        )
        or path.startswith(("alice_platform/", "config/"))
        or (path.startswith("tests/") and path != "tests/test_ci_platform_changes.py")
        or path in {"requirements.txt", "requirements-dev.txt", "pyproject.toml"}
        for path in paths
    )
    database = any(
        "postgres" in path.lower()
        or path.startswith(("migrations/",))
        or path in {"provider_credentials.py", "requirements-postgres.txt"}
        for path in paths
    )

    # The web application is the one intentional cross-platform integration surface.
    if web:
        backend = True
        android = True

    return {
        "web": web,
        "backend": backend,
        "android": android,
        "infra": infra,
        "database": database,
    }


def main() -> int:
    result = classify([line.strip() for line in sys.stdin if line.strip()])
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
