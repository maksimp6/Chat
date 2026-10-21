"""Execute the real workflow shell with a fake Python transport, never a model."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/semantic-work.yml").read_text())


def test_workflow_requires_owner_first_attempt_and_explicit_budget():
    data = workflow()
    events = data.get("on", data.get(True))
    assert set(events) == {"workflow_dispatch", "push"}
    assert events["workflow_dispatch"]["inputs"]["allow_model_call"]["default"] is False
    assert events["workflow_dispatch"]["inputs"]["allow_vector_store_write"]["default"] is False
    assert events["workflow_dispatch"]["inputs"]["mode"]["default"] == "search"
    assert "vector-store" in events["workflow_dispatch"]["inputs"]["mode"]["options"]
    assert events["workflow_dispatch"]["inputs"]["corpus"]["default"] == "tasks"
    assert data["concurrency"]["cancel-in-progress"] is False
    guard = data["jobs"]["semantic"]["if"]
    assert "github.event_name == 'workflow_dispatch'" in guard
    assert "github.actor == github.repository_owner" in guard
    assert "github.triggering_actor == github.repository_owner" in guard
    assert "github.run_attempt == '1'" in guard
    demo_guard = data["jobs"]["vector-store-demo"]["if"]
    assert "github.event.before == '41a388a2c309423a4c100c3d1fec683c620a5d37'" in demo_guard
    assert (
        "github.event.head_commit.message == "
        "'feat(vector-store): add task snapshot upload and direct search'" in demo_guard
    )
    assert "github.event.before == 'c21ab3fda8ec9e17fcd4acdc84edaa52a6387ddc'" in demo_guard
    assert (
        "github.event.head_commit.message == 'fix(vector-store): report safe runner diagnostics'"
        in demo_guard
    )
    assert "github.actor == github.repository_owner" in demo_guard
    assert data["jobs"]["vector-store-demo"]["permissions"] == {
        "contents": "read",
        "issues": "read",
    }


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
        "SEM_CORPUS": "tasks",
        "SEM_PUBLISH_ISSUE": issue,
        "SEM_ALLOW_MODEL": allow,
        "SEM_APPROVAL_REFERENCE": reference,
    }
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
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
            "--corpus",
            "tasks",
            "--query-file",
            str(query_path),
            *expected_args,
        ]


def test_vector_store_dispatch_requires_write_approval_and_reference(tmp_path):
    capture = tmp_path / "invocation.json"
    fake = tmp_path / "python"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json,os,pathlib,sys\n"
        "pathlib.Path(os.environ['CAPTURE']).write_text(json.dumps(sys.argv[1:]))\n"
    )
    fake.chmod(0o755)
    script = workflow()["jobs"]["semantic"]["steps"][-1]["run"]
    script = script.replace("/tmp/sem_query.txt", str(tmp_path / "query.txt"))
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "CAPTURE": str(capture),
        "SEM_QUERY": "симуляция офиса",
        "SEM_MODE": "vector-store",
        "SEM_CORPUS": "tasks",
        "SEM_PUBLISH_ISSUE": "",
        "SEM_ALLOW_MODEL": "false",
        "SEM_ALLOW_VECTOR_STORE_WRITE": "true",
        "SEM_APPROVAL_REFERENCE": "owner-confirmed-vector-store-demo",
    }
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0
    assert json.loads(capture.read_text()) == [
        "scripts/semantic_pipeline.py",
        "--mode",
        "vector-store",
        "--corpus",
        "tasks",
        "--query-file",
        str(tmp_path / "query.txt"),
        "--allow-vector-store-write",
        "--approval-reference",
        "owner-confirmed-vector-store-demo",
    ]
