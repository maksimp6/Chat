from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_runner_cache_contract() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "cache-dependency-path: package.json" in workflow
    assert "cache: npm" in workflow
    assert "requirements-postgres.txt" in workflow
    assert workflow.count("cache: pip") >= 2
    assert "cache: gradle" not in workflow
    assert "gradle/actions/setup-gradle@" in workflow


def test_runner_cache_timings_are_reported() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "Application dependency install:" in workflow
    assert "PostgreSQL dependency install:" in workflow
    assert "Android test/build:" in workflow
    assert '$GITHUB_STEP_SUMMARY' in workflow
