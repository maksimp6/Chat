#!/usr/bin/env python3
"""Select affected platform suites and reject unsupported successful CI results."""

from __future__ import annotations

import json
import os
from pathlib import PurePosixPath
import sys
from typing import Mapping

PLATFORMS = ("web", "backend", "android", "infra", "database", "mcp")
JOBS = ("backend", "postgres", "android", "infra", "mcp")
SHARED_INPUTS = {
    ".github/workflows/ci.yml",
    ".python-version",
    ".coveragerc",
    "pytest.ini",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "scripts/format.sh",
}
INFRA_TEST_PREFIXES = (
    "tests/test_cloudru",
    "tests/test_ci_",
    "tests/test_platform",
    "tests/test_rdc",
    "tests/test_remote_desktop_commander",
    "tests/test_preview_deploy",
    "tests/test_production_deploy",
)
MCP_PREFIXES = (
    "deploy/chrome-worker/",
    "deploy/oauth-idp/",
    "deploy/remote-desktop-commander/",
)


def _runtime_platforms(path: str) -> set[str]:
    if (
        path
        in {
            "db.py",
            "db_backend.py",
            "memory_db.py",
            "provider_credentials.py",
            "requirements-postgres.txt",
        }
        or path.startswith("migrations/")
        or "postgres" in path.lower()
    ):
        return {"backend", "database"}
    if path.startswith("tests/") and path.endswith(".py"):
        return {"backend"}
    if path.endswith(".py"):
        # Keep SQL compatibility coverage until the runtime migration is complete.
        return {"backend", "database"}
    if path.startswith(("docs/", ".github/ISSUE_TEMPLATE/", ".agents/")) or path.endswith(".md"):
        return set()
    return set(PLATFORMS)


def _path_platforms(path: str) -> set[str]:
    if (
        not path
        or path.startswith("/")
        or ".." in PurePosixPath(path).parts
        or path in SHARED_INPUTS
    ):
        return set(PLATFORMS)
    if path.startswith("android/"):
        return {"android"}
    if (
        path.startswith(("static/", "templates/"))
        or path in {"app.py", "package.json", "package-lock.json"}
        or (path.startswith("tests/") and path.endswith(".js"))
    ):
        return {"web"}
    if path.startswith(MCP_PREFIXES):
        return {"mcp", "infra"}
    if (
        path.startswith(INFRA_TEST_PREFIXES)
        or path.startswith(
            (
                "cloud/",
                "scripts/cloudru_",
                "scripts/ci_",
                "alice_platform/",
                "config/alice/",
                ".github/workflows/",
            )
        )
        or path == "requirements-deploy.txt"
    ):
        return {"infra"}
    return _runtime_platforms(path)


def classify(paths: list[str]) -> dict[str, bool]:
    """Unknown inputs select every suite rather than silently dropping tests."""
    selected: set[str] = set() if paths else set(PLATFORMS)
    for path in paths:
        selected.update(_path_platforms(path))
    if "web" in selected:
        selected.update(("backend", "android", "database", "mcp"))
    return {platform: platform in selected for platform in PLATFORMS}


def validate_results(plan: Mapping[str, bool], results: Mapping[str, str]) -> None:
    """A selected suite must succeed; only unselected suites may be skipped."""
    if set(plan) != set(PLATFORMS) or any(type(value) is not bool for value in plan.values()):
        raise ValueError("invalid platform plan")
    if plan["web"] and not all(plan[key] for key in ("backend", "android", "database", "mcp")):
        raise ValueError("incomplete web dependency plan")
    for job in ("changes", "code-rules"):
        if results.get(job) != "success":
            raise ValueError(f"required check failed: {job}")
    for job in JOBS:
        required = plan["database"] if job == "postgres" else plan[job]
        allowed = {"success"} if required else {"success", "skipped"}
        if results.get(job) not in allowed:
            raise ValueError(f"platform check missing, failed or unexpectedly skipped: {job}")


def verify_environment() -> None:
    raw = json.loads(os.environ["CI_PLATFORM_PLAN"])
    needs = json.loads(os.environ["CI_JOB_RESULTS"])
    if (
        not isinstance(raw, dict)
        or set(raw) != set(PLATFORMS)
        or any(value not in ("true", "false") for value in raw.values())
        or not isinstance(needs, dict)
    ):
        raise ValueError("invalid platform plan/results")
    plan = {key: value == "true" for key, value in raw.items()}
    results = {}
    for job in ("changes", "code-rules", *JOBS):
        record = needs.get(job)
        if not isinstance(record, dict) or not isinstance(record.get("result"), str):
            raise ValueError(f"missing job result: {job}")
        results[job] = record["result"]
    validate_results(plan, results)


def main() -> int:
    mode = sys.argv[1:]
    try:
        if mode == ["--verify"]:
            verify_environment()
            print("All selected platform suites passed")
        elif mode == ["--all"]:
            print(json.dumps(dict.fromkeys(PLATFORMS, True), sort_keys=True))
        elif mode in ([], ["--null"]):
            data = sys.stdin.read()
            paths = data.split("\0") if mode else data.splitlines()
            print(json.dumps(classify([path for path in paths if path]), sort_keys=True))
        else:
            raise ValueError("unknown routing mode")
    except (KeyError, ValueError):
        print("Platform routing verification failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
