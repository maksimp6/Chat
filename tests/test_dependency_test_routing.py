"""Dependency manifest updates must select the suites that exercise their runtime."""

import pytest

from scripts.ci_platform_changes import classify


ALL = {"web", "backend", "android", "infra", "database"}
WEB = {"web", "backend", "android"}


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("requirements.txt", ALL),
        ("requirements-dev.txt", ALL),
        ("requirements-deploy.txt", {"infra"}),
        ("android/requirements.txt", {"android"}),
        ("android/app/build.gradle.kts", {"android"}),
        ("package.json", WEB),
        ("package-lock.json", WEB),
    ],
)
def test_dependency_manifest_changes_run_their_code_tests(path, expected):
    plan = classify([path])
    assert {name for name, selected in plan.items() if selected} == expected
