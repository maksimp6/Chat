import yaml
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_runner_cache_contract() -> None:
    source = CI_WORKFLOW.read_text(encoding="utf-8")
    jobs = yaml.safe_load(source)["jobs"]
    dependencies = {
        "code-rules": ["requirements-dev.txt"],
        "backend": ["requirements.txt", "requirements-dev.txt"],
        "infra": ["requirements.txt", "requirements-dev.txt"],
        "postgres": ["requirements.txt", "requirements-postgres.txt", "requirements-dev.txt"],
    }
    for job, expected in dependencies.items():
        steps = [
            s for s in jobs[job]["steps"] if s.get("uses", "").startswith("actions/setup-python@")
        ]
        assert len(steps) == 1
        config = steps[0]["with"]
        assert config["cache"] == "pip"
        assert config["cache-dependency-path"].splitlines() == expected
        assert (
            config.get("python-version") == "3.14"
            or config.get("python-version-file") == ".python-version"
        )

    npm_dependencies = {
        "backend": ["package.json"],
        "mcp": [
            "deploy/chrome-worker/package-lock.json",
            "deploy/remote-desktop-commander/package-lock.json",
        ],
    }
    for job, expected in npm_dependencies.items():
        steps = [
            s for s in jobs[job]["steps"] if s.get("uses", "").startswith("actions/setup-node@")
        ]
        assert len(steps) == 1
        config = steps[0]["with"]
        assert config["node-version"] == "22.22.2"
        assert config["cache"] == "npm"
        assert config["cache-dependency-path"].splitlines() == expected

    assert "cache: gradle" not in source
    assert source.count("uses: gradle/actions/setup-gradle@") == 1


def test_runner_cache_timings_are_reported() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert (
        'echo "- Application dependency install: $((SECONDS - started))s" '
        '>> "$GITHUB_STEP_SUMMARY"' in workflow
    )
    assert (
        'echo "- PostgreSQL dependency install: $((SECONDS - started))s" '
        '>> "$GITHUB_STEP_SUMMARY"' in workflow
    )
    assert (
        'echo "- Android test/build: $((SECONDS - started))s" >> "$GITHUB_STEP_SUMMARY"' in workflow
    )
