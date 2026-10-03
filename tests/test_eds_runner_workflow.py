import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def test_eds_credentials_are_scoped_to_authorized_task_execution():
    jobs = workflow("claude-lite.yml")["jobs"]
    job = jobs["claude"]
    assert job["environment"] == "production"
    assert "OWNER" in job["if"] and "COLLABORATOR" in job["if"]
    assert "env" not in job
    steps = job["steps"]
    install = next(step for step in steps if step["name"] == "Install verified EDS CLI")
    execute = next(step for step in steps if step["name"] == "Run Claude Code")
    assert steps.index(install) < steps.index(execute)
    assert install["if"] == execute["if"]
    assert "timeout 30s python scripts/install_eds.py" in install["run"]
    assert '"$GITHUB_PATH"' in install["run"]
    assert execute["env"] == {
        "EDS_API_KEY": "${{ secrets.EDS_API_KEY }}",
        "EDS_PROJECT_ID": "${{ secrets.EDS_PROJECT_ID }}",
    }
    for step in steps:
        if step is not execute:
            assert "EDS_API_KEY" not in str(step)
    assert "EDS_API_KEY" not in str(jobs["dialogue"])


def test_eds_auth_check_is_manual_trusted_and_does_not_publish_api_output():
    config = workflow("eds-runner.yml")
    # PyYAML's YAML 1.1 resolver treats the Actions 'on' key as boolean True.
    assert config[True] == {"workflow_dispatch": None}
    assert config["permissions"] == {"contents": "read"}
    job = config["jobs"]["check"]
    assert job["if"] == "github.ref == 'refs/heads/master'"
    assert job["environment"] == "production"
    steps = job["steps"]
    assert steps[0]["with"]["persist-credentials"] is False
    assert all("EDS_API_KEY" not in str(step) for step in steps[:-1])
    check = steps[-1]
    assert check["env"]["EDS_API_KEY"] == "${{ secrets.EDS_API_KEY }}"
    assert check["env"]["EDS_PROJECT_ID"] == "${{ secrets.EDS_PROJECT_ID }}"
    assert '-z "$EDS_API_KEY"' in check["run"]
    assert "timeout 30s eds repo list --limit 1 --offset 0 --json >/dev/null 2>&1" in check["run"]
    assert "eds config" not in check["run"]
    assert not any("upload-artifact" in step.get("uses", "") for step in steps)


@pytest.mark.parametrize("exit_code", [0, 1])
def test_live_check_never_logs_cli_output(tmp_path, exit_code):
    binary = tmp_path / "eds"
    binary.write_text(
        f'#!/bin/bash\necho "$EDS_API_KEY"\necho "$EDS_API_KEY" >&2\nexit {exit_code}\n'
    )
    binary.chmod(0o700)
    command = workflow("eds-runner.yml")["jobs"]["check"]["steps"][-1]["run"]
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", command],
        env={
            "PATH": f"{tmp_path}:{os.defpath}",
            "EDS_API_KEY": "synthetic-secret-must-never-be-logged",
            "EDS_PROJECT_ID": "synthetic-project",
        },
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == exit_code
    assert "synthetic-secret" not in result.stdout + result.stderr
    assert ("access verified" in result.stdout) is (exit_code == 0)


def test_missing_keys_stop_before_any_cli_request(tmp_path):
    binary = tmp_path / "eds"
    binary.write_text('#!/bin/bash\ntouch "$MARKER"\n')
    binary.chmod(0o700)
    marker = tmp_path / "called"
    command = workflow("eds-runner.yml")["jobs"]["check"]["steps"][-1]["run"]
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", command],
        env={"PATH": f"{tmp_path}:{os.defpath}", "MARKER": str(marker)},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 1
    assert "Missing EDS_API_KEY or EDS_PROJECT_ID" in result.stdout
    assert not marker.exists()
