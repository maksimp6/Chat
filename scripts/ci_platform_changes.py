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


def classify(paths: list[str]) -> dict[str, bool]:
    """Unknown inputs select every suite rather than silently dropping tests."""
    selected: set[str] = set()
    if not paths:
        selected.update(PLATFORMS)
    for path in paths:
        if (
            not path
            or path.startswith("/")
            or ".." in PurePosixPath(path).parts
            or path in SHARED_INPUTS
        ):
            selected.update(PLATFORMS)
        elif path.startswith("android/"):
            selected.add("android")
        elif (
            path.startswith(("static/", "templates/"))
            or path in {"app.py", "package.json", "package-lock.json"}
            or (path.startswith("tests/") and path.endswith(".js"))
        ):
            selected.add("web")
        elif path.startswith(MCP_PREFIXES):
            selected.update(("mcp", "infra"))
        elif (
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
            selected.add("infra")
        elif (
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
            selected.update(("backend", "database"))
        elif path.startswith("tests/") and path.endswith(".py"):
            selected.add("backend")
        elif path.endswith(".py"):
            # Until SQL migration is complete, runtime Python changes may affect
            # either backend. This is compatibility coverage, not an Android build.
            selected.update(("backend", "database"))
        elif path.startswith(("docs/", ".github/ISSUE_TEMPLATE/", ".agents/")) or path.endswith(
            ".md"
        ):
            continue
        else:
            selected.update(PLATFORMS)
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
