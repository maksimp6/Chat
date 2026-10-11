"""Dependency manifest updates must select the suites that exercise their runtime."""

import pytest

from scripts.ci_platform_changes import classify


ALL = {"web", "backend", "android", "infra", "mcp"}
WEB = {"web", "backend", "android", "mcp"}


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("requirements.txt", ALL),
        ("requirements-dev.txt", ALL),
        ("requirements-postgres.txt", {"backend"}),
        ("requirements-deploy.txt", {"infra"}),
        ("android/requirements.txt", {"android"}),
        ("android/app/build.gradle.kts", {"android"}),
        ("package.json", WEB),
        ("package-lock.json", WEB),
        ("deploy/chrome-worker/package.json", {"infra", "mcp"}),
        ("deploy/chrome-worker/package-lock.json", {"infra", "mcp"}),
        ("deploy/oauth-idp/package.json", {"infra", "mcp"}),
        ("deploy/remote-desktop-commander/package.json", {"infra", "mcp"}),
    ],
)
def test_dependency_manifest_changes_run_their_code_tests(path, expected):
    plan = classify([path])
    assert {name for name, selected in plan.items() if selected} == expected
