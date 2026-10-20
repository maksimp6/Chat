"""Execute the real workflow shell with a fake Python transport, never a model."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/semantic-work.yml").read_text())


def test_workflow_requires_owner_first_attempt_and_explicit_budget():
    data = workflow()
    events = data.get("on", data.get(True))
    assert set(events) == {"workflow_dispatch"}
    assert events["workflow_dispatch"]["inputs"]["allow_model_call"]["default"] is False
    assert data["concurrency"]["cancel-in-progress"] is False
    guard = data["jobs"]["semantic"]["if"]
    assert "github.actor == github.repository_owner" in guard
    assert "github.triggering_actor == github.repository_owner" in guard
    assert "github.run_attempt == '1'" in guard


@pytest.mark.parametrize(
    "allow,reference,issue,expected_code,expected_args",
    [
        ("false", "", "", 0, []),
        ("true", "", "", 2, None),
        (
            "true",
            "owner-approved-budget",
            "9000",
            0,
            ["--publish-issue", "9000", "--allow-model-call"],
        ),
    ],
)
def test_workflow_shell_admission_and_literal_query(
    allow, reference, issue, expected_code, expected_args, tmp_path
):
    capture = tmp_path / "invocation.json"
    fake = tmp_path / "python"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json,os,pathlib,sys\n"
        "pathlib.Path(os.environ['CAPTURE']).write_text(json.dumps(sys.argv[1:]))\n"
    )
    fake.chmod(0o755)
    query_path = tmp_path / "query.txt"
    script = workflow()["jobs"]["semantic"]["steps"][-1]["run"]
    script = script.replace("/tmp/sem_query.txt", str(query_path))
    query = '$(touch SHOULD_NOT_EXIST) `touch ALSO_NOT` ; echo "literal"'
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "CAPTURE": str(capture),
        "SEM_QUERY": query,
        "SEM_MODE": "query",
        "SEM_PUBLISH_ISSUE": issue,
        "SEM_ALLOW_MODEL": allow,
        "SEM_APPROVAL_REFERENCE": reference,
    }
    result = subprocess.run(
        ["bash", "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == expected_code
    assert query_path.read_text() == query + "\n"
    assert not (tmp_path / "SHOULD_NOT_EXIST").exists()
    assert not (tmp_path / "ALSO_NOT").exists()
    if expected_args is None:
        assert not capture.exists()
    else:
        assert json.loads(capture.read_text()) == [
            "scripts/semantic_pipeline.py",
            "--mode",
            "query",
            "--query-file",
            str(query_path),
            *expected_args,
        ]
