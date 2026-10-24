"""Exercise the real one-step shell, admission boundary and result delivery offline."""

import base64
import hashlib
import importlib.util
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "logos_once_under_test", ROOT / "scripts/logos_once.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/logos-once.yml").read_text())


def environment():
    return {
        "LOGOS_QUESTION": 'Разбери $(touch escaped) `touch escaped` "дело"\nбез потерь',
        "LOGOS_CASE_ISSUE": "1413",
        "LOGOS_CASE_PR": "1435",
        "LOGOS_GENERATION_ALLOWED": "true",
        "GITHUB_REPOSITORY": "maksimp6/Chat",
        "GITHUB_REPOSITORY_OWNER": "maksimp6",
        "YANDEX_API_KEY": "synthetic-never-log-this",
        "GITHUB_TOKEN": "synthetic-github-token",
    }


def test_one_manual_job_one_named_step_and_no_silent_condition():
    data = workflow()
    assert set(data.get("on", data.get(True))) == {"workflow_dispatch"}
    assert set(data["jobs"]) == {"logos"}
    job = data["jobs"]["logos"]
    assert job["name"] == "logos" and "if" not in job
    assert len(job["steps"]) == 1
    step = job["steps"][0]
    assert step["name"] == "logos" and "if" not in step
    assert all(value == "read" for value in job["permissions"].values())
    assert "${{ inputs." not in step["run"]
    assert "persist-credentials" not in step["run"]
    assert "credential.helper=" in step["run"]
    subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)


@pytest.mark.parametrize("flag", ["", "false", "true", "TRUE"])
def test_actual_court_hold_blocks_before_any_subprocess_or_question_file(
    tmp_path, monkeypatch, flag
):
    env = environment()
    env["LOGOS_GENERATION_ALLOWED"] = flag
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: pytest.fail("external work"))
    result, code = runner.run_native(ROOT, tmp_path, env)
    assert code == 1 and result["status"] == "blocked"
    assert result["error"] == "NativeLogosLaunchBlocked"
    assert result["generation_requests"] == 0
    assert result["response_ids"] == []
    assert not (tmp_path / "logos-question.txt").exists()


@pytest.mark.parametrize(
    "field,value",
    [("LOGOS_QUESTION", ""), ("LOGOS_CASE_ISSUE", "1; x"), ("LOGOS_CASE_PR", "0")],
)
def test_invalid_input_has_no_external_effect(tmp_path, monkeypatch, field, value):
    env = environment()
    env[field] = value
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: pytest.fail("external work"))
    result, code = runner.run_native(ROOT, tmp_path, env)
    assert code == 1 and result["runner_phase"] == "input"


def admit_only_for_offline_transport_test(monkeypatch):
    from agent_runtime import logos_launch_hold

    monkeypatch.setattr(logos_launch_hold, "enforce_native_launch_hold", lambda: None)


def test_separate_generation_gate_still_blocks(tmp_path, monkeypatch):
    admit_only_for_offline_transport_test(monkeypatch)
    env = environment()
    env["LOGOS_GENERATION_ALLOWED"] = "false"
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: pytest.fail("external work"))
    result, code = runner.run_native(ROOT, tmp_path, env)
    assert code == 1 and result["error"] == "GenerationNotPermitted"


def test_native_cli_sequence_preserves_question_and_exact_result(tmp_path, monkeypatch):
    admit_only_for_offline_transport_test(monkeypatch)
    calls = []
    expected = {
        "status": "completed",
        "reply": "Ответ\n" * 20000,
        "response_ids": ["receipt"],
    }

    def transport(command, **kwargs):
        calls.append(command)
        assert kwargs["check"] is True and kwargs["capture_output"] is True
        if "scripts.logos_context" in command:
            assert kwargs["env"]["GITHUB_TOKEN"] == "synthetic-github-token"
            assert "YANDEX_API_KEY" not in kwargs["env"]
        elif "scripts.logos_knowledge" in command:
            assert "GITHUB_TOKEN" not in kwargs["env"]
        elif "scripts.logos_dialogue" not in command:
            assert "YANDEX_API_KEY" not in kwargs["env"] and "GITHUB_TOKEN" not in kwargs["env"]
        if "scripts.logos_dialogue" in command:
            Path(command[command.index("--out") + 1]).write_text(json.dumps(expected))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", transport)
    monkeypatch.setattr(
        runner,
        "run_dialogue_command",
        lambda command, root, env, answer: transport(
            command, env=env, check=True, capture_output=True
        ),
    )
    result, code = runner.run_native(ROOT, tmp_path, environment())
    assert code == 0 and result == expected
    assert len(calls) == 5
    assert [c[2] for c in calls[2:]] == [
        "scripts.logos_context",
        "scripts.logos_knowledge",
        "scripts.logos_dialogue",
    ]
    assert (tmp_path / "logos-question.txt").read_text() == environment()["LOGOS_QUESTION"]
    assert not (tmp_path / "escaped").exists()


def test_failed_dialogue_keeps_checkpoint_and_never_retries_or_logs_raw_error(
    tmp_path, monkeypatch, capsys
):
    admit_only_for_offline_transport_test(monkeypatch)
    calls = []
    saved = {
        "status": "incomplete",
        "reply": "Сохранённая часть",
        "response_ids": ["pending-id"],
    }

    def transport(command, **kwargs):
        calls.append(command)
        if "scripts.logos_dialogue" in command:
            Path(command[command.index("--out") + 1]).write_text(json.dumps(saved))
            raise subprocess.CalledProcessError(
                7,
                command,
                output="synthetic-never-log-this",
                stderr="synthetic-never-log-this",
            )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", transport)
    monkeypatch.setattr(
        runner,
        "run_dialogue_command",
        lambda command, root, env, answer: transport(command, check=True, capture_output=True),
    )
    result, code = runner.run_native(ROOT, tmp_path, environment())
    assert code == 1 and len(calls) == 5
    assert result["reply"] == saved["reply"] and result["response_ids"] == ["pending-id"]
    assert result["status"] == "failed" and result["runner_phase"] == "dialogue"
    assert result["native_status"] == "incomplete"
    assert "synthetic-never-log-this" not in capsys.readouterr().out + json.dumps(result)


def test_invalid_result_does_not_crash_error_report(tmp_path, monkeypatch):
    admit_only_for_offline_transport_test(monkeypatch)

    def transport(command, **kwargs):
        if "scripts.logos_dialogue" in command:
            Path(command[command.index("--out") + 1]).write_text("[]")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", transport)
    monkeypatch.setattr(
        runner,
        "run_dialogue_command",
        lambda command, root, env, answer: transport(command, check=True, capture_output=True),
    )
    result, code = runner.run_native(ROOT, tmp_path, environment())
    assert code == 1 and result["runner_error"] == "TypeError"


def test_log_chunks_reconstruct_complete_unicode_and_do_not_execute_workflow_commands(
    tmp_path, capsys
):
    reply = "Юникод\n::error::not a command\r<script>not HTML</script>" * 10000
    result = {"status": "completed", "reply": reply, "response_ids": ["exact-response"]}
    summary = tmp_path / "summary.md"
    runner.emit_result(result, summary)
    lines = capsys.readouterr().out.splitlines()
    parts = [line.split(":", 1)[1] for line in lines if line.startswith("LOGOS_RESULT_PART=")]
    payload = base64.b64decode("".join(parts))
    assert json.loads(payload) == result
    assert "LOGOS_RESULT_SHA256=" + hashlib.sha256(payload).hexdigest() in lines
    assert all(not line.startswith("::") for line in lines)
    assert "<script>" not in summary.read_text()


@pytest.mark.parametrize("older_than_30s,expected_exit", [(False, 1), (True, 0)])
def test_real_shell_preserves_bootstrap_failure_and_owner_duration_policy(
    tmp_path, older_than_30s, expected_exit
):
    tools = tmp_path / "bin"
    tools.mkdir()
    workspace = tmp_path / "checkout"
    workspace.mkdir()
    git = tools / "git"
    git.write_text(
        f"#!{sys.executable}\nimport sys\nraise SystemExit(17 if 'fetch' in sys.argv else 0)\n"
    )
    git.chmod(0o755)
    python = tools / "python3"
    python.write_text(
        f"#!{sys.executable}\nimport os, sys, time\n"
        "if len(sys.argv) > 2 and sys.argv[1] == '-c':\n"
        f" print(time.monotonic_ns() - {31000000000 if older_than_30s else 0})\n"
        "else:\n os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n"
    )
    python.chmod(0o755)
    script = workflow()["jobs"]["logos"]["steps"][0]["run"]
    result = subprocess.run(
        ["bash", "-c", script],
        check=False,
        env={
            **os.environ,
            "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "GITHUB_REPOSITORY": "maksimp6/Chat",
            "GITHUB_WORKSPACE": str(workspace),
            "GITHUB_SHA": "a" * 40,
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == expected_exit, result.stdout + result.stderr
    assert "execution_exit=17" in result.stdout
    assert f"overridden={str(older_than_30s).lower()}" in result.stdout


@pytest.mark.parametrize("exit_code", [0, 7])
def test_checkpoint_is_exported_before_native_process_finishes(
    tmp_path, monkeypatch, capsys, exit_code
):
    answer = tmp_path / "answer.json"
    checkpoint = {
        "status": "running",
        "response_ids": ["already-created-response"],
        "reply": "Полученная часть",
    }

    class Process:
        def __enter__(self):
            self.polls = 0
            return self

        def __exit__(self, *args):
            return False

        def poll(self):
            self.polls += 1
            if self.polls == 1:
                answer.write_text(json.dumps(checkpoint))
                return None
            assert "LOGOS_CHECKPOINT_PART=" in capsys.readouterr().out
            return exit_code

    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: Process())
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)
    if exit_code:
        with pytest.raises(subprocess.CalledProcessError):
            runner.run_dialogue_command(["offline-native"], ROOT, environment(), answer)
    else:
        runner.run_dialogue_command(["offline-native"], ROOT, environment(), answer)


@pytest.mark.parametrize("elapsed", [29_999_000_000, 30_000_000_000, 30_001_000_000])
@pytest.mark.parametrize("execution_exit", [0, 7])
def test_inline_finalizer_preserves_exact_owner_boundary(
    tmp_path, monkeypatch, capsys, elapsed, execution_exit
):
    script = workflow()["jobs"]["logos"]["steps"][0]["run"]
    finalizer = script.split("python3 -I - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    monkeypatch.setenv("JOB_STARTED_NS", "100")
    monkeypatch.setenv("LOGOS_EXECUTION_EXIT", str(execution_exit))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    monkeypatch.setattr(runner.time, "monotonic_ns", lambda: 100 + elapsed)
    expected = int(bool(execution_exit) and elapsed <= 30_000_000_000)
    finalizer_file = tmp_path / "inline_finalizer.py"
    finalizer_file.write_text(finalizer)
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(finalizer_file), run_name="__main__")
    assert stopped.value.code == expected
    assert f"execution_exit={execution_exit}" in capsys.readouterr().out
