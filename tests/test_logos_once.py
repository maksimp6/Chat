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


