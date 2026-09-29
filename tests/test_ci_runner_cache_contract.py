from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_runner_cache_contract() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    backend_cache_block = """\
          cache: pip
          cache-dependency-path: |
            requirements.txt
            requirements-dev.txt
"""
    postgres_cache_block = """\
          cache: pip
          cache-dependency-path: |
            requirements.txt
            requirements-postgres.txt
            requirements-dev.txt
"""
    npm_cache_block = """\
          cache: npm
          cache-dependency-path: package.json
"""

    assert backend_cache_block in workflow
    assert postgres_cache_block in workflow
    assert npm_cache_block in workflow
    assert "cache: gradle" not in workflow
    assert "gradle/actions/setup-gradle@" in workflow


def test_runner_cache_timings_are_reported() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert (
        'echo "- Application dependency install: $((SECONDS - started))s" '
        '>> "$GITHUB_STEP_SUMMARY"'
    ) in workflow
    assert (
        'echo "- PostgreSQL dependency install: $((SECONDS - started))s" '
        '>> "$GITHUB_STEP_SUMMARY"'
    ) in workflow
    assert (
        'echo "- Android test/build: $((SECONDS - started))s" '
        '>> "$GITHUB_STEP_SUMMARY"'
    ) in workflow
