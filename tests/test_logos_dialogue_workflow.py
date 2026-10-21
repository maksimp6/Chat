"""Check the manual dialogue workflow without contacting a provider."""

from pathlib import Path
import json
import os
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/rag-work.yml").read_text())


def test_dialogue_is_manual_with_native_runtime_and_safe_checkout():
    data = workflow()
    events = data.get("on", data.get(True))
    assert "allow_model_call" not in events["workflow_dispatch"]["inputs"]
    job = data["jobs"]["logos"]
    assert job["permissions"] == {"contents": "read"}
    for condition in (
        "github.event_name == 'workflow_dispatch'",
        "inputs.mode == 'logos'",
    ):
        assert condition in job["if"]
    assert data["concurrency"]["cancel-in-progress"] is False
    assert "inputs.mode != 'logos'" in data["jobs"]["rag"]["if"]
    checkout = next(step for step in job["steps"] if step["name"] == "Checkout repository")
    assert checkout["with"]["persist-credentials"] is False
    artifact = job["steps"][-1]["with"]["path"]
    assert artifact == "${{ runner.temp }}/logos-answer.json"


@pytest.mark.parametrize(
    "question,accepted",
    [
        ("", False),
        ('почему $(touch escaped) `touch escaped` "Иисуса"', True),
    ],
)
def test_question_is_required_and_passed_literally(tmp_path, question, accepted):
    script = workflow()["jobs"]["logos"]["steps"][0]["run"]
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env={
            **os.environ,
            "RUNNER_TEMP": str(tmp_path),
            "LOGOS_QUESTION": question,
        },
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert (result.returncode == 0) is accepted
    assert not (tmp_path / "escaped").exists()
    question_file = tmp_path / "logos-question.txt"
    if accepted:
        assert question_file.read_text() == question
    else:
        assert not question_file.exists()


def test_saved_result_job_is_manual_read_only_and_has_no_provider_credentials():
    data = workflow()
    job = data["jobs"]["logos-result"]
    assert data["permissions"] == {}
    assert job["permissions"] == {"actions": "read"}
    assert "github.event_name == 'workflow_dispatch'" in job["if"]
    assert "inputs.mode == 'logos-result'" in job["if"]
    assert "inputs.mode != 'logos-result'" in data["jobs"]["rag"]["if"]
    assert len(job["steps"]) == 1
    assert job["steps"][0]["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "GH_REPO": "${{ github.repository }}",
        "LOGOS_SOURCE_RUN": "${{ inputs.query }}",
    }


def run_saved_result(tmp_path, source_run, saved_result):
    """Run the real workflow shell/Python with only the GitHub transport replaced."""
    tools = tmp_path / "bin"
    tools.mkdir()
    call_file = tmp_path / "github-call.json"
    artifact = tmp_path / "saved-answer.json"
    artifact.write_text(json.dumps(saved_result), encoding="utf-8")
    gh = tools / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, shutil, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "Path(os.environ['FAKE_GH_CALL']).write_text(json.dumps(\n"
        "    {'args': args, 'repo': os.environ['GH_REPO']}))\n"
        "destination = Path(args[args.index('--dir') + 1])\n"
        "destination.mkdir(parents=True)\n"
        "shutil.copyfile(os.environ['FAKE_ARTIFACT'], destination / 'logos-answer.json')\n"
    )
    gh.chmod(0o755)
    (tools / "python").symlink_to(sys.executable)
    script = workflow()["jobs"]["logos-result"]["steps"][0]["run"]
    result = subprocess.run(
        ["/bin/bash", "-c", script],
        cwd=tmp_path,
        env={
            "PATH": str(tools),
            "RUNNER_TEMP": str(tmp_path),
            "GH_TOKEN": "offline-token",
            "GH_REPO": "maksimp6/Chat",
            "LOGOS_SOURCE_RUN": source_run,
            "FAKE_GH_CALL": str(call_file),
            "FAKE_ARTIFACT": str(artifact),
        },
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result, call_file


def test_saved_result_downloads_requested_run_and_prints_original_json_on_one_line(tmp_path):
    saved = {
        "status": "completed",
        "reply": 'Ответ из артефакта.\n::error::literal text\r$(touch escaped) "quote"',
        "generation_requests": 1,
        "response_ids": ["original-provider-response"],
    }
    result, call_file = run_saved_result(tmp_path, "39625558554", saved)

    assert result.returncode == 0, result.stderr
    assert json.loads(call_file.read_text()) == {
        "args": [
            "run",
            "download",
            "39625558554",
            "--name",
            "logos-answer-39625558554",
            "--dir",
            str(tmp_path / "logos-result"),
        ],
        "repo": "maksimp6/Chat",
    }
    lines = result.stdout.splitlines()
    assert len(lines) == 1 and lines[0].startswith("LOGOS_RESULT=")
    assert json.loads(lines[0].removeprefix("LOGOS_RESULT=")) == saved
    assert not (tmp_path / "escaped").exists()


@pytest.mark.parametrize(
    "source_run", ["", "0", "--help", "123; touch escaped", "$(touch escaped)", "123\n456"]
)
def test_saved_result_rejects_invalid_run_id_before_download(tmp_path, source_run):
    result, call_file = run_saved_result(tmp_path, source_run, {})

    assert result.returncode != 0
    assert "A numeric run ID is required." in result.stdout
    assert not call_file.exists()
    assert not (tmp_path / "escaped").exists()


def test_stored_response_mode_routes_to_read_only_cli_with_literal_id(tmp_path):
    data = workflow()
    job = data["jobs"]["logos"]
    assert "inputs.mode == 'logos-response'" in job["if"]
    assert "inputs.mode != 'logos-response'" in data["jobs"]["rag"]["if"]
    step = next(step for step in job["steps"] if "LOGOS_MODE" in step.get("env", {}))
    assert step["env"]["LOGOS_MODE"] == "${{ inputs.mode }}"
    tools = tmp_path / "bin"
    tools.mkdir()
    python = tools / "python"
    python.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "if sys.argv[1] == '-c':\n"
        "    print('offline-test-encryption-key')\n"
        "else:\n"
        "    Path(os.environ['FAKE_PYTHON_CALL']).write_text(json.dumps(sys.argv[1:]))\n"
    )
    python.chmod(0o755)
    call_file = tmp_path / "python-call.json"
    untrusted_id = '$(touch escaped) `touch escaped` "quoted"\nnext line'
    (tmp_path / "logos-question.txt").write_text(untrusted_id)

    result = subprocess.run(
        ["/bin/bash", "-c", step["run"]],
        cwd=tmp_path,
        env={
            "PATH": str(tools) + ":/usr/bin:/bin",
            "RUNNER_TEMP": str(tmp_path),
            "LOGOS_MODE": "logos-response",
            "FAKE_PYTHON_CALL": str(call_file),
        },
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "escaped").exists()
    assert json.loads(call_file.read_text()) == [
        "-m",
        "scripts.logos_dialogue",
        "--response-id",
        untrusted_id,
        "--out",
        str(tmp_path / "logos-answer.json"),
    ]
