"""CI and security checks run on pull requests only; pushes do not trigger them."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
GATED = ("ci.yml", "security.yml")


def _load(name: str) -> dict:
    # BaseLoader keeps the unquoted `on:` key as a string instead of YAML 1.1 True.
    return yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


@pytest.mark.parametrize("name", GATED)
def test_push_trigger_is_disabled(name):
    assert "push" not in _load(name)["on"]


@pytest.mark.parametrize("name", GATED)
def test_pull_request_trigger_is_preserved(name):
    assert "pull_request" in _load(name)["on"]


@pytest.mark.parametrize("name", GATED)
def test_no_pull_request_target_trigger(name):
    # pull_request_target runs with base-repo secrets; it must stay out of these checks.
    assert "pull_request_target" not in _load(name)["on"]


def test_ci_does_not_cancel_superseded_develop_pushes():
    # Pushes to develop/master are integration evidence: never cancel them mid-run.
    concurrency = _load("ci.yml")["concurrency"]
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def test_ci_required_aggregator_still_gates_every_platform_job():
    jobs = _load("ci.yml")["jobs"]
    required = jobs["required"]
    assert required["name"] == "CI required"
    assert {"code-rules", "backend", "infra"} <= set(required["needs"])


@pytest.mark.parametrize("name", GATED)
def test_workflow_permissions_are_least_privilege(name):
    permissions = _load(name)["permissions"]
    assert permissions == {"contents": "read"}
