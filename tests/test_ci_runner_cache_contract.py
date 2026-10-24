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

    backend_node = [
        s for s in jobs["backend"]["steps"] if s.get("uses", "").startswith("actions/setup-node@")
    ]
    assert len(backend_node) == 1
    config = backend_node[0]["with"]
    assert config["node-version"] == "22.22.2"
    assert "cache" not in config
    assert "npm install" not in source

    assert "cache: gradle" not in source
    assert "gradle/actions/setup-gradle@" not in source


def test_runner_cache_timings_are_reported() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert (
        'echo "- Application dependency install: $((SECONDS - started))s" '
        '>> "$GITHUB_STEP_SUMMARY"' in workflow
    )
