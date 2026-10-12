"""Integration and stacked PRs must receive the checks used for admission."""

from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("filename", ["ci.yml", "format.yml", "security.yml", "codeql.yml"])
@pytest.mark.parametrize("base", ["master", "develop", "feature/stack-parent"])
def test_required_workflows_accept_integration_and_stacked_prs(filename, base):
    workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
    # PyYAML's YAML 1.1 loader treats GitHub's `on` key as boolean True.
    events = workflow[True]
    assert "pull_request" in events
    trigger = events["pull_request"] or {}
    assert not trigger.get("branches-ignore")
    branches = trigger.get("branches")
    assert branches is None or base in branches
    assert not trigger.get("paths")
    assert not trigger.get("paths-ignore")
    assert "synchronize" in trigger.get("types", ["opened", "synchronize", "reopened"])


def test_integration_required_check_runs_even_when_dependencies_fail():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    required = workflow["jobs"]["required"]
    assert required["name"] == "CI required"
    assert required["if"] == "always()"
    assert set(required["needs"]) == {
        "changes",
        "code-rules",
        "backend",
        "postgres",
        "android",
        "infra",
        "mcp",
    }
    assert any("--verify" in step.get("run", "") for step in required["steps"])
