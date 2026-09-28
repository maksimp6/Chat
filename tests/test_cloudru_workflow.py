"""Contract tests for Cloud.ru Workflow Studio delegation."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml


WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github/workflows/cloudru-deploy.yml"


def _workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text())


def _workflow_step(name):
    return next(step for step in _workflow()["jobs"]["cloudru"]["steps"] if step["name"] == name)


def test_workflow_delegates_source_fetch_and_build_to_cloudru_services():
    text = WORKFLOW_PATH.read_text()
    workflow = _workflow()
    events = workflow.get("on", workflow.get(True))

    assert "master" in events["push"]["branches"]
    assert "actions/checkout" not in text
    assert "scripts/cloudru_deploy.py" not in text
    assert "CLOUDRU_IAM_KEY" not in text
    assert "CLOUDRU_WORKFLOW_API_KEY" in text
    assert "X-API-KEY" in text
    assert "/deployment" in text


@pytest.mark.parametrize(
    "event_name,action,missing,success",
    [
        ("workflow_dispatch", "preflight", None, True),
        ("workflow_dispatch", "deploy", "CLOUDRU_WORKFLOW_API_KEY", False),
        ("workflow_dispatch", "status", "CLOUDRU_WORKFLOW_APP_ID", False),
        ("push", "deploy", "CLOUDRU_WORKFLOW_APP_ID", True),
    ],
)
def test_workflow_preflight_validates_without_exposing_secret_values(
    event_name, action, missing, success
):
    step = _workflow_step("Validate configuration")
    env = {
        "GITHUB_EVENT_NAME": event_name,
        "ACTION": action,
        "DEPLOY_REF": "master",
        "CLOUDRU_PROJECT_ID": "project",
        "CLOUDRU_WORKFLOW_APP_ID": "app",
        "CLOUDRU_WORKFLOW_API_KEY": "private-test-value",
        "GITHUB_OUTPUT": os.devnull,
    }
    if missing:
        env.pop(missing)

    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        env=env,
        text=True,
        capture_output=True,
    )
    assert (result.returncode == 0) is success
    assert "private-test-value" not in result.stdout + result.stderr
    if missing and not success:
        assert missing in result.stdout
