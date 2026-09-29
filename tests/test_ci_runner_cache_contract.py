import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _steps_named(workflow: str, name: str) -> list[str]:
    return re.findall(
        rf"(?ms)^      - name: {re.escape(name)}\n(.*?)(?=^      - name:|\Z)",
        workflow,
    )


def test_runner_cache_contract() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    python_steps = [
        step for step in _steps_named(workflow, "Set up Python") if "cache: pip" in step
    ]
    assert len(python_steps) == 2
    assert "uses: actions/setup-python@" in python_steps[0]
    assert """with:
          python-version: "3.14"
          cache: pip
          cache-dependency-path: |
            requirements.txt
            requirements-dev.txt""" in python_steps[0]
    assert "uses: actions/setup-python@" in python_steps[1]
    assert """with:
          python-version: "3.14"
          cache: pip
          cache-dependency-path: |
            requirements.txt
            requirements-postgres.txt
            requirements-dev.txt""" in python_steps[1]

    node_steps = _steps_named(workflow, "Set up Node")
    assert len(node_steps) == 1
    assert "uses: actions/setup-node@" in node_steps[0]
    assert """with:
          node-version: "22.22.2"
          cache: npm
          cache-dependency-path: package.json""" in node_steps[0]

    assert "cache: gradle" not in workflow
    assert workflow.count("uses: gradle/actions/setup-gradle@") == 1


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
