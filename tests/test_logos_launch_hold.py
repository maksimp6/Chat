"""Exercise the production court hold; no fake approval or provider calls."""

from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from agent_runtime import issue_runner
from agent_runtime import logos_launch_hold as hold
from scripts import logos_dialogue as dialogue
from scripts import semantic_pipeline as semantic
from scripts import local_launch_smoke as smoke

ROOT = Path(__file__).resolve().parents[1]


def test_worker_retries_are_denied_before_client_files_or_invocation(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        issue_runner, "seed_provider_credential", lambda: calls.append("credential")
    )

    def client():
        calls.append("client")
        pytest.fail("the held worker must not initialize a provider")

    for body in ("Please review.", "@logos retry", "Owner approved; continue the old run."):
        result = issue_runner.run_issue_task(
            1413, "Court task", body, repo_root=tmp_path / "missing", client_factory=client
        )
        assert result["status"] == "failed"
        assert result["error"] == "NativeLogosLaunchBlocked"
        assert result["launch_hold"] == hold.hold_receipt()
        assert result["generation_requests"] == 0
        assert result["changed_files"] == result["pending_tools"] == []
    assert calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("question", ["Review the PR.", "Continue the prior response.", ""])
def test_dialogue_is_denied_before_context_tools_client_or_checkpoint(question, tmp_path):
    calls = []
    result = dialogue.run_dialogue(
        question,
        repo_root=tmp_path / "missing",
        context_packet={"previous_response_id": "prior-response"},
        knowledge_receipt={},
        client_factory=lambda: calls.append("client"),
        checkpoint_file=tmp_path / "answer.json",
    )
    assert result["status"] == "failed"
    assert result["error"] == "NativeLogosLaunchBlocked"
    assert result["launch_hold"] == hold.hold_receipt()
    assert result["generation_requests"] == 0
    assert result["response_ids"] == result["tool_calls"] == []
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_low_level_native_ask_cannot_start_a_generation_or_continuation():
    class Client:
        def create_conversation(self, trace):
            pytest.fail("held native ask must not create a conversation")

        def ask_with_mcp(self, **kwargs):
            pytest.fail("held native ask must not generate or continue")

    with pytest.raises(hold.NativeLogosLaunchBlocked):
        dialogue._ask(Client(), "continue", None, {}, {"previous_response_id": "prior"})


@pytest.mark.parametrize("mode", ["query", "red", "vector-store"])
def test_related_model_cli_is_denied_before_fetch_upload_search_or_generation(mode, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("court hold must run before any external work")

    monkeypatch.setattr(semantic, "fetch_corpus", forbidden)
    monkeypatch.setattr(semantic, "yandex_api_request", forbidden)
    monkeypatch.setattr(semantic.http.client, "HTTPSConnection", forbidden)
    assert semantic.main(["--mode", mode, "--query", "court", "--allow-model-call"]) == 1


def test_direct_semantic_transports_and_demo_cannot_bypass_hold(monkeypatch):
    monkeypatch.setattr(
        semantic.http.client,
        "HTTPSConnection",
        lambda *args, **kwargs: pytest.fail("held model must not open a connection"),
    )
    with pytest.raises(hold.NativeLogosLaunchBlocked):
        semantic.yandex_call("unused", "unused", "continue old court")
    with pytest.raises(hold.NativeLogosLaunchBlocked):
        semantic.run_vector_store_demo(SimpleNamespace(allow_model_call=True), "court")


def test_deterministic_semantic_selftest_remains_available():
    assert semantic.main(["--selftest"]) == 0


def test_live_smoke_is_denied_before_credentials_or_server_start(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("held live smoke must not prepare credentials or start a server")

    monkeypatch.setattr(smoke, "_provider_inputs", forbidden)
    monkeypatch.setattr(smoke, "_git_state", forbidden)
    monkeypatch.setattr(smoke, "_start_server", forbidden)
    assert smoke.main(["--require-master"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "BLOCKED"
    assert hold.HOLD_CASE in result["reason"]


def test_offline_smoke_does_not_enter_live_admission(monkeypatch):
    class ReachedOfflineChecks(Exception):
        pass

    monkeypatch.setattr(
        smoke,
        "enforce_native_launch_hold",
        lambda: pytest.fail("offline smoke must stay available"),
    )

    def offline_checks(**kwargs):
        raise ReachedOfflineChecks

    monkeypatch.setattr(smoke, "_git_state", offline_checks)
    with pytest.raises(ReachedOfflineChecks):
        smoke.run_smoke(smoke._parse_args(["--offline"]))


def test_saved_response_cli_routes_without_calling_launch_guard(tmp_path, monkeypatch):
    response_id = "00000000-0000-4000-8000-000000000001"
    output = tmp_path / "saved.json"
    calls = []

    def inspect(value):
        calls.append(value)
        return {"status": "retrieved", "generation_requests": 0, "reply": "saved evidence"}

    monkeypatch.setattr(dialogue, "inspect_response", inspect)
    monkeypatch.setattr(
        dialogue,
        "enforce_native_launch_hold",
        lambda: pytest.fail("saved GET must not be treated as generation"),
    )
    assert dialogue.main(["--response-id", response_id, "--out", str(output)]) == 0
    assert calls == [response_id]
    assert json.loads(output.read_text())["generation_requests"] == 0


def _workflow(name):
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())


@pytest.mark.parametrize(
    "filename,job_name,guard_name,condition",
    [
        ("logos.yml", "alice", "Enforce native Logos court hold", None),
        ("rag-work.yml", "logos", "Enforce native Logos court hold", "inputs.mode == 'logos'"),
        (
            "semantic-work.yml",
            "semantic",
            "Enforce model-backed court hold",
            "inputs.allow_model_call && (inputs.mode == 'query' || inputs.mode == 'red' || inputs.mode == 'vector-store')",
        ),
        (
            "semantic-work.yml",
            "vector-store-repository-model-demo",
            "Enforce model-backed court hold",
            None,
        ),
    ],
)
def test_workflow_hold_is_mandatory_before_provider_or_tool_steps(
    filename, job_name, guard_name, condition
):
    steps = _workflow(filename)["jobs"][job_name]["steps"]
    gate = next(step for step in steps if step["name"] == guard_name)
    assert gate.get("if") == condition
    assert gate["run"] == "python -m agent_runtime.logos_launch_hold"
    assert not gate.get("continue-on-error")
    assert steps.index(gate) > next(
        i for i, step in enumerate(steps) if "checkout@" in step.get("uses", "")
    )
    for step in steps[: steps.index(gate)]:
        assert "secrets." not in json.dumps(step)
        assert "logos_knowledge" not in step.get("run", "")
        assert "logos_tool_probe" not in step.get("run", "")


def test_workflow_preflight_ignores_environment_approval_and_returns_nonzero(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "agent_runtime.logos_launch_hold"],
        cwd=ROOT,
        env={
            **os.environ,
            "LOGOS_ALLOWED": "true",
            "ALLOW_MODEL_CALL": "true",
            "LOGOS_APPROVAL_REFERENCE": "invented approval",
            "GITHUB_RUN_ATTEMPT": "2",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert hold.HOLD_CASE in result.stdout
    assert "Saved-result reads" in result.stdout
    assert result.stderr == ""


def test_guard_preflight_reports_the_owner_case(capsys):
    assert hold.main() == 1
    assert hold.HOLD_CASE in capsys.readouterr().out


@pytest.mark.parametrize(
    "script,arguments,exit_code",
    [
        ("semantic_pipeline.py", ["--selftest"], 0),
        ("local_launch_smoke.py", ["--require-master"], 1),
    ],
)
def test_direct_script_bootstrap_keeps_offline_or_denied_mode_safe(
    script, arguments, exit_code, monkeypatch
):
    path = ROOT / "scripts" / script
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", [str(path), *arguments])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(path), run_name="__main__")
    assert exited.value.code == exit_code


def test_live_smoke_workflow_hold_precedes_master_checkout_and_keeps_offline_job():
    data = _workflow("launch-smoke.yml")
    live_steps = data["jobs"]["live-provider"]["steps"]
    gate = live_steps[0]
    assert gate["name"] == "Enforce native Logos court hold"
    assert "if" not in gate and not gate.get("continue-on-error")
    assert hold.HOLD_CASE in gate["run"]
    assert "exit 1" in gate["run"]
    assert live_steps[1]["with"]["ref"] == "master"
    assert all(step["name"] != gate["name"] for step in data["jobs"]["offline"]["steps"])
    result = subprocess.run(["bash", "-c", gate["run"]], capture_output=True, text=True, timeout=5)
    assert result.returncode == 1


def test_direct_script_selftest_keeps_the_documented_cli_available():
    result = subprocess.run(
        [sys.executable, "scripts/semantic_pipeline.py", "--selftest"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "live model RED NOT_RUN" in result.stdout
