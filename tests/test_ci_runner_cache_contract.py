from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_runner_cache_contract() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    backend_pip_cache = """          cache: pip
          cache-dependency-path: |
            requirements.txt
            requirements-dev.txt"""
    postgres_pip_cache = """          cache: pip
          cache-dependency-path: |
            requirements.txt
            requirements-postgres.txt
            requirements-dev.txt"""
    npm_cache = """          cache: npm
          cache-dependency-path: package.json"""

    assert backend_pip_cache in workflow
    assert postgres_pip_cache in workflow
    assert npm_cache in workflow
    assert "cache: gradle" not in workflow
    assert "gradle/actions/setup-gradle@" in workflow


def test_runner_cache_timings_are_reported() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    application_timing = (
        'echo "- Application dependency install: $((SECONDS - started))s" '
        '>> "$GITHUB_STEP_SUMMARY"'
    )
    postgres_timing = (
        'echo "- PostgreSQL dependency install: $((SECONDS - started))s" '
        '>> "$GITHUB_STEP_SUMMARY"'
    )
    android_timing = (
        'echo "- Android test/build: $((SECONDS - started))s" '
        '>> "$GITHUB_STEP_SUMMARY"'
    )

    assert application_timing in workflow
    assert postgres_timing in workflow
    assert android_timing in workflow
