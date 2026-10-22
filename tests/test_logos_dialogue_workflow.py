"""Check the manual dialogue workflow without contacting a provider."""

from pathlib import Path
import hashlib
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


def test_tool_audit_routes_without_question_or_provider_credentials():
    data = workflow()
    job = data["jobs"]["logos"]
    assert "inputs.mode == 'logos-tools'" in job["if"]
    assert "inputs.mode != 'logos-tools'" in data["jobs"]["rag"]["if"]
    assert "env" not in data and "env" not in job
    steps = {step["name"]: step for step in job["steps"]}
    assert steps["Save question"]["if"] == "inputs.mode != 'logos-tools'"
    provider = steps["Run Logos dialogue or read a saved provider response"]
    assert provider["if"] == "inputs.mode != 'logos-tools'"
    audit = steps["Audit native Logos tools"]
    assert audit["if"] == "inputs.mode == 'logos-tools'"
    assert "env" not in audit
    assert "secrets." not in json.dumps(audit)
    assert "logos_dialogue" not in audit["run"]
    assert steps["Save dialogue result"]["if"] == "always()"


@pytest.mark.parametrize("probe_exit", [0, 7])
def test_tool_audit_saves_exact_json_and_preserves_probe_failure(tmp_path, probe_exit):
    job = workflow()["jobs"]["logos"]
    step = next(step for step in job["steps"] if step["name"] == "Audit native Logos tools")
    tools = tmp_path / "bin"
    tools.mkdir()
    python = tools / "python"
    python.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "assert sys.argv[1:] == ['-m', 'scripts.logos_tool_probe', '--external']\n"
        "print(json.dumps({'native_tool_audit': True, 'generation_requests': 0}))\n"
        f"raise SystemExit({probe_exit})\n"
    )
    python.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash", "-c", step["run"]],
        cwd=tmp_path,
        env={"PATH": str(tools) + ":/usr/bin:/bin", "RUNNER_TEMP": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == probe_exit, result.stderr
    artifact = (tmp_path / "logos-answer.json").read_text()
    assert artifact == result.stdout
    assert json.loads(artifact) == {"native_tool_audit": True, "generation_requests": 0}
    assert not (tmp_path / "logos-question.txt").exists()


def test_native_changes_capture_is_separate_from_provider_and_always_saved():
    job = workflow()["jobs"]["logos"]
    steps = {step["name"]: step for step in job["steps"]}
    capture = steps["Capture native Logos changes for review"]
    assert capture["if"] == "always() && inputs.mode == 'logos'"
    assert capture["env"] == {"LOGOS_BASE_SHA": "${{ github.sha }}"}
    assert capture["run"].startswith("python -I - <<'PY'")
    assert "secrets." not in json.dumps(capture)
    artifact = steps["Save native Logos changes for review"]
    assert artifact["if"] == capture["if"]
    assert artifact["with"]["name"] == "logos-changes-${{ github.run_id }}"
    assert artifact["with"]["path"] == "${{ runner.temp }}/logos-changes.json"
    assert artifact["with"]["retention-days"] == 7


def test_native_changes_capture_records_real_diff_without_unrelated_secret_contents(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    temporary = tmp_path / "runner"
    temporary.mkdir()

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()

    git("init", "-q")
    (repo / "tool_providers").mkdir()
    (repo / "tests").mkdir()
    source = repo / "tool_providers/registry.py"
    source.write_text("original = True\n")
    secret = repo / "unrelated-secret.txt"
    secret.write_text("OLD-SECRET-CONTENT\n")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    source.write_text("repaired = True\n")
    new_test = repo / "tests/test_logos_tool_repairs.py"
    new_test.write_text("def test_native_repair():\n    assert True\n")
    secret.write_text("NEW-SECRET-CONTENT\n")
    (repo / "untracked-secret.txt").write_text("UNTRACKED-SECRET-CONTENT\n")
    (repo / "json.py").write_text("raise RuntimeError('CHECKOUT-MODULE-EXECUTED')\n")
    job = workflow()["jobs"]["logos"]
    step = next(
        step for step in job["steps"] if step["name"] == "Capture native Logos changes for review"
    )
    result = subprocess.run(
        ["/bin/bash", "-c", step["run"]],
        cwd=repo,
        env={**os.environ, "LOGOS_BASE_SHA": base, "RUNNER_TEMP": str(temporary)},
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 1 and lines[0].startswith("LOGOS_CHANGES=")
    evidence = json.loads(lines[0].removeprefix("LOGOS_CHANGES="))
    assert evidence == json.loads((temporary / "logos-changes.json").read_text())
    assert evidence["base_sha"] == evidence["head_sha"] == base
    assert evidence["changed_paths"] == [
        "json.py",
        "tests/test_logos_tool_repairs.py",
        "tool_providers/registry.py",
        "unrelated-secret.txt",
        "untracked-secret.txt",
    ]
    assert "+repaired = True" in evidence["patch"]
    assert "+def test_native_repair()" in evidence["patch"]
    assert evidence["patch_sha256"] == hashlib.sha256(evidence["patch"].encode()).hexdigest()
    assert "SECRET-CONTENT" not in result.stdout
    assert "CHECKOUT-MODULE-EXECUTED" not in result.stdout + result.stderr
    assert "unrelated-secret.txt" not in evidence["patch"]
    assert "untracked-secret.txt" not in evidence["patch"]
    assert git("rev-parse", "HEAD") == base
    assert "tests/test_logos_tool_repairs.py" in git("ls-files")
