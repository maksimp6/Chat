import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "logos.yml").read_text(encoding="utf-8")
JOB = yaml.safe_load(WORKFLOW)["jobs"]["alice"]
STEPS = {step["name"]: step for step in JOB["steps"]}


def _step_run(name: str) -> str:
    return STEPS[name]["run"]


def _run_step(name, tmp_path, env):
    return subprocess.run(
        ["bash", "-e", "-c", _step_run(name)],
        cwd=tmp_path,
        env={**os.environ, **env},
        text=True,
        capture_output=True,
    )


def _mock_tools(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = """#!/usr/bin/env -S python3 -I
import json
import os
import sys
from pathlib import Path

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["COMMAND_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"tool": tool, "args": args}) + "\\n")
if tool == "gh":
    failure = os.environ.get("MOCK_GH_FAILURE", "")
    if failure and " ".join(args[:2]) == failure:
        sys.exit(1)
    if args[:2] == ["pr", "create"]:
        print("https://github.com/example/repo/pull/123")
    elif args[:1] == ["api"]:
        print("42")
    elif args[:2] == ["issue", "comment"]:
        body = Path(args[args.index("--body-file") + 1]).read_text(encoding="utf-8")
        Path(os.environ["COMMENT_COPY"]).write_text(body, encoding="utf-8")
"""
    for name in ("gh", "git"):
        script = bin_dir / name
        script.write_text(stub, encoding="utf-8")
        script.chmod(0o755)
    return {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "COMMAND_LOG": str(tmp_path / "commands.jsonl"),
        "COMMENT_COPY": str(tmp_path / "comment.md"),
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(tmp_path / "outputs"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "GITHUB_RUN_ID": "99",
        "GITHUB_SERVER_URL": "https://github.com",
        "ISSUE_NUMBER": "17",
        "REPOSITORY": "example/repo",
        "GH_TOKEN": "test-token",
        "APP_SLUG": "",
        "WORKFLOW_STATUS": "success",
        "APP_TOKEN_OUTCOME": "success",
        "PR_URL": "",
        "EVIDENCE_URL": "https://github.com/example/repo/actions/runs/99/artifacts/123",
        "EVIDENCE_OUTCOME": "success",
    }


def _commands(tmp_path):
    path = tmp_path / "commands.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _result(tmp_path, **overrides):
    result = {
        "status": "no_changes",
        "model": "logos-runtime",
        "summary": "Ask @logos again",
        "reply": "Ask @logos again",
        "reply_state": "available",
        "billing": {"total_cost": 0.12, "currency": "RUB", "cost_status": "calculated"},
        "trace_id": "t1",
        "pending_tools": ["delete_path"],
    }
    result.update(overrides)
    (tmp_path / "result.json").write_text(json.dumps(result), encoding="utf-8")


def test_actions_are_pinned_and_do_not_persist_credentials():
    uses = re.findall(r"uses:\s*(\S+)", WORKFLOW)
    assert uses
    assert all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref) for ref in uses)
    assert "persist-credentials: false" in WORKFLOW
    assert "pull_request_target" not in WORKFLOW
    assert "permissions: {}" in WORKFLOW


def test_only_trusted_mentions_or_label_trigger_alice():
    condition = JOB["if"]
    assert condition.count("contains(github.event.comment.body, '@logos')") == 1
    assert condition.count('["OWNER","MEMBER","COLLABORATOR"]') == 2
    assert "github.event.label.name == 'alice'" in condition
    assert "!github.event.issue.pull_request" in condition


def test_untrusted_text_never_reaches_run_scripts_directly():
    for step in JOB["steps"]:
        assert "${{" not in step.get("run", "")
    for field in ("issue.title", "issue.body", "comment.body"):
        assert f"${{{{ github.event.{field} }}}}" in WORKFLOW


def test_secrets_are_scoped_to_their_steps():
    assert "secrets.YANDEX_API_KEY" in STEPS["Run Alice"]["env"]["YANDEX_API_KEY"]
    assert "secrets." not in json.dumps(JOB.get("env", {}))
    github_steps = {
        "Check App credentials",
        "Create Logos app token",
        "Require publishing token",
        "Open pull request",
        "Report result",
    }
    for name, step in STEPS.items():
        if "secrets.ALICE_" in json.dumps(step):
            assert name in github_steps
    assert "ALICE_GITHUB_TOKEN" not in json.dumps(STEPS["Run Alice"])


def _git_repo(tmp_path: Path, files):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for name in files:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    return repo


@pytest.mark.parametrize(
    ("files", "allowed"),
    [
        (["module.py"], True),
        ([".github/workflows/ci.yml"], False),
        ([".env"], False),
        (["android/release.jks"], False),
        ([f"f{i}.py" for i in range(21)], False),
    ],
)
def test_diff_guard(tmp_path, files, allowed):
    repo = _git_repo(tmp_path, files)
    result = subprocess.run(
        ["bash", "-e", "-c", _step_run("Guard the diff")],
        cwd=repo,
        env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
        capture_output=True,
    )
    assert (result.returncode == 0) is allowed


def test_issue_text_is_saved_verbatim(tmp_path):
    env = {
        "RUNNER_TEMP": str(tmp_path),
        "ISSUE_TITLE": "Fix $(touch pwned) `id`",
        "ISSUE_BODY": "Body",
        "COMMENT_BODY": "@logos go",
    }
    result = _run_step("Save issue text", tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "title.txt").read_text() == "Fix $(touch pwned) `id`\n"
    assert "@logos go" in (tmp_path / "body.txt").read_text()
    assert not (tmp_path / "pwned").exists()


@pytest.mark.parametrize(
    ("client", "key", "ready"),
    [
        ("", "", False),
        ("client", "", False),
        ("", "private-key", False),
        ("client", "private-key", True),
    ],
)
def test_app_token_requires_both_credentials(tmp_path, client, key, ready):
    output = tmp_path / "outputs"
    result = _run_step(
        "Check App credentials",
        tmp_path,
        {"APP_CLIENT_ID": client, "APP_PRIVATE_KEY": key, "GITHUB_OUTPUT": str(output)},
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text().splitlines() == [f"ready={str(ready).lower()}"]
    assert "private-key" not in result.stdout + result.stderr + output.read_text()


def test_app_failure_preserves_personal_token_fallback():
    step = STEPS["Create Logos app token"]
    assert step["id"] == "app-token"
    assert "always()" in step["if"]
    assert "steps.app-credentials.outputs.ready == 'true'" in step["if"]
    assert step["continue-on-error"] is True
    assert re.fullmatch(r"actions/create-github-app-token@[0-9a-f]{40}", step["uses"])
    assert step["with"]["private-key"] == "${{ secrets.ALICE_APP_PRIVATE_KEY }}"
    permissions = {
        key: value for key, value in step["with"].items() if key.startswith("permission-")
    }
    assert permissions == {
        "permission-contents": "write",
        "permission-issues": "write",
        "permission-pull-requests": "write",
    }
    for name in ("Require publishing token", "Open pull request", "Report result"):
        assert (
            STEPS[name]["env"]["GH_TOKEN"]
            == "${{ steps.app-token.outputs.token || secrets.ALICE_GITHUB_TOKEN }}"
        )
    assert "steps.app-token.outputs.token &&" in STEPS["Open pull request"]["env"]["APP_SLUG"]


@pytest.mark.parametrize("token", ["", "test-token"])
def test_missing_token_blocks_before_model_call(tmp_path, token):
    env = _mock_tools(tmp_path)
    env["GH_TOKEN"] = token
    result = _run_step("Require publishing token", tmp_path, env)
    assert (result.returncode == 0) is bool(token)
    names = list(STEPS)
    assert names.index("Require publishing token") < names.index("Run Alice")
    assert "always()" not in STEPS["Run Alice"].get("if", "")
    assert "|| echo" not in _step_run("Run Alice")


def test_revoked_token_fails_read_preflight_before_model(tmp_path):
    env = _mock_tools(tmp_path)
    env["MOCK_GH_FAILURE"] = "api --silent"
    result = _run_step("Require publishing token", tmp_path, env)
    assert result.returncode != 0
    assert _commands(tmp_path) == [
        {"tool": "gh", "args": ["api", "--silent", "repos/example/repo"]}
    ]


def test_publication_is_success_gated_and_report_always_runs():
    assert STEPS["Open pull request"]["if"] == "success()"
    assert STEPS["Report result"]["if"] == "always()"
    assert STEPS["Check App credentials"]["if"] == "always()"


@pytest.mark.parametrize("app_slug", ["", "logos-maksimp6"])
def test_pull_request_targets_develop_with_correct_identity(tmp_path, app_slug):
    env = _mock_tools(tmp_path)
    env["APP_SLUG"] = app_slug
    (tmp_path / "files.txt").write_text("module.py\n", encoding="utf-8")
    _result(tmp_path, status="changed")
    result = _run_step("Open pull request", tmp_path, env)
    assert result.returncode == 0, result.stderr
    calls = _commands(tmp_path)
    pr = next(
        call["args"]
        for call in calls
        if call["tool"] == "gh" and call["args"][:2] == ["pr", "create"]
    )
    assert pr[pr.index("--base") + 1] == "develop"
    assert "--draft" in pr
    git_calls = [call["args"] for call in calls if call["tool"] == "git"]
    assert ["config", "user.name", f"{app_slug}[bot]" if app_slug else "Logos"] in git_calls
    if app_slug:
        assert [
            "config",
            "user.email",
            "42+logos-maksimp6[bot]@users.noreply.github.com",
        ] in git_calls
    assert "https://github.com/example/repo/pull/123" in (tmp_path / "outputs").read_text()


def test_no_changes_do_not_open_pull_request(tmp_path):
    env = _mock_tools(tmp_path)
    (tmp_path / "files.txt").write_text("", encoding="utf-8")
    result = _run_step("Open pull request", tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert _commands(tmp_path) == []


def test_report_escapes_mentions_everywhere_and_preserves_cost(tmp_path):
    env = _mock_tools(tmp_path)
    _result(tmp_path, model="@logos", pending_tools=["@logos"], trace_id="@logos")
    result = _run_step("Report result", tmp_path, env)
    assert result.returncode == 0, result.stderr
    report = (tmp_path / "report.md").read_text()
    assert "@logos" not in report
    assert "0.12 RUB (calculated)" in report
    assert (tmp_path / "comment.md").read_text() == report
    assert report in (tmp_path / "summary.md").read_text()


def test_report_ignores_repository_python_startup_with_negative_control(tmp_path):
    env = _mock_tools(tmp_path)
    env["GH_TOKEN"] = "synthetic-report-token"
    env["WORKFLOW_STATUS"] = "failure"
    env["PYTHONPATH"] = str(tmp_path)
    marker = tmp_path / "startup-token-probe"
    env["STARTUP_PROBE"] = str(marker)
    (tmp_path / "sitecustomize.py").write_text(
        "import os\nfrom pathlib import Path\n"
        'Path(os.environ["STARTUP_PROBE"]).write_text(os.environ["GH_TOKEN"])\n',
        encoding="utf-8",
    )
    _result(tmp_path)
    result = _run_step("Report result", tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    assert (tmp_path / "comment.md").exists()

    unisolated = _step_run("Report result").replace("python -I - ", "python - ", 1)
    assert unisolated != _step_run("Report result")
    control = subprocess.run(
        ["bash", "-e", "-c", unisolated],
        cwd=tmp_path,
        env={**os.environ, **env},
        text=True,
        capture_output=True,
    )
    assert control.returncode == 0, control.stderr
    assert marker.read_text() == "synthetic-report-token"


def test_guard_failure_is_reported_without_publishing_changes(tmp_path):
    env = _mock_tools(tmp_path)
    env["WORKFLOW_STATUS"] = "failure"
    env["APP_TOKEN_OUTCOME"] = "failure"
    (tmp_path / "files.txt").write_text(".github/workflows/unsafe.yml\n", encoding="utf-8")
    _result(tmp_path, status="changed")
    _run_step("Report result", tmp_path, env)
    report = (tmp_path / "report.md").read_text()
    assert "failure" in report
    assert (tmp_path / "comment.md").read_text() == report
    assert not any(
        call["tool"] == "git" or call["args"][:2] == ["pr", "create"]
        for call in _commands(tmp_path)
    )


def test_missing_token_still_writes_summary_without_api_calls(tmp_path):
    env = _mock_tools(tmp_path)
    env["GH_TOKEN"] = ""
    env["APP_TOKEN_OUTCOME"] = "failure"
    _result(tmp_path)
    result = _run_step("Report result", tmp_path, env)
    assert result.returncode != 0
    report = (tmp_path / "report.md").read_text()
    assert report in (tmp_path / "summary.md").read_text()
    assert "personal-token fallback is available" not in report
    assert _commands(tmp_path) == []


@pytest.mark.parametrize(
    "raw_result", [None, "not json", "[]", "{}", '{"status":"changed","pending_tools":1}']
)
def test_missing_or_invalid_result_is_reported_then_fails(tmp_path, raw_result):
    env = _mock_tools(tmp_path)
    if raw_result is not None:
        (tmp_path / "result.json").write_text(raw_result, encoding="utf-8")
    result = _run_step("Report result", tmp_path, env)
    assert result.returncode != 0
    report = (tmp_path / "report.md").read_text()
    assert report in (tmp_path / "summary.md").read_text()
    assert (tmp_path / "comment.md").read_text() == report


def test_failed_model_result_is_reported_then_fails(tmp_path):
    env = _mock_tools(tmp_path)
    _result(tmp_path, status="failed")
    result = _run_step("Report result", tmp_path, env)
    assert result.returncode != 0
    assert "failed" in (tmp_path / "comment.md").read_text()
    assert (tmp_path / "report.md").read_text() in (tmp_path / "summary.md").read_text()


def test_report_preserves_refusal_machine_reason_without_treating_it_as_review(tmp_path):
    env = _mock_tools(tmp_path)
    _result(
        tmp_path,
        status="failed",
        provider_status="incomplete",
        provider_reason="content_filter",
        provider_error_present=False,
        summary="Refusal",
        reply="Refusal",
    )
    run = _run_step("Report result", tmp_path, env)
    assert run.returncode != 0
    report = (tmp_path / "comment.md").read_text()
    assert "provider_reason: `content_filter`" in report
    assert "**failed**" in report
    assert report in (tmp_path / "summary.md").read_text()


def test_report_rejects_nonnull_provider_error_even_with_success_worker_status(tmp_path):
    env = _mock_tools(tmp_path)
    _result(
        tmp_path,
        provider_error_present=True,
        provider_error_code="server_error",
        provider_error_type="ghp_synthetic-secret",
        provider_reason="private\nerror",
    )
    run = _run_step("Report result", tmp_path, env)
    assert run.returncode != 0
    report = (tmp_path / "comment.md").read_text()
    assert "provider_error_code: `server_error`" in report
    assert "non-null error" in report
    assert "ghp_synthetic-secret" not in report
    assert "private\nerror" not in report


def test_failed_pull_request_still_gets_a_final_report(tmp_path):
    env = _mock_tools(tmp_path)
    env["MOCK_GH_FAILURE"] = "pr create"
    (tmp_path / "files.txt").write_text("module.py\n", encoding="utf-8")
    _result(tmp_path, status="changed")
    opened = _run_step("Open pull request", tmp_path, env)
    assert opened.returncode != 0
    assert any(call["args"][:2] == ["pr", "create"] for call in _commands(tmp_path))
    env["WORKFLOW_STATUS"] = "failure"
    _run_step("Report result", tmp_path, env)
    assert "failure" in (tmp_path / "comment.md").read_text()


def test_app_authentication_failure_uses_available_fallback_for_report(tmp_path):
    env = _mock_tools(tmp_path)
    env["APP_TOKEN_OUTCOME"] = "failure"
    _result(tmp_path)
    result = _run_step("Report result", tmp_path, env)
    assert result.returncode == 0, result.stderr
    report = (tmp_path / "comment.md").read_text()
    assert "personal-token fallback is available" in report


def test_comment_api_failure_does_not_lose_summary(tmp_path):
    env = _mock_tools(tmp_path)
    env["MOCK_GH_FAILURE"] = "issue comment"
    _result(tmp_path)
    result = _run_step("Report result", tmp_path, env)
    assert result.returncode != 0
    assert (tmp_path / "report.md").read_text() in (tmp_path / "summary.md").read_text()


def test_response_artifact_runs_before_publishing_even_after_model_failure():
    step = STEPS["Preserve response evidence"]
    assert step["if"] == "always()"
    assert step["with"]["path"] == "${{ runner.temp }}/result.json"
    assert step["with"]["if-no-files-found"] == "error"
    assert step["with"]["retention-days"] == 90
    assert "github.run_attempt" in step["with"]["name"]
    names = list(STEPS)
    assert names.index("Run Alice") < names.index("Preserve response evidence")
    assert names.index("Preserve response evidence") < names.index("Open pull request")
    assert names.index("Preserve response evidence") < names.index("Report result")


def test_long_response_survives_publisher_failure_with_verifiable_artifact(tmp_path):
    import hashlib

    env = _mock_tools(tmp_path)
    env["MOCK_GH_FAILURE"] = "issue comment"
    reply = "Полный ответ Ё🙂 " * 800 + " END  \n"
    _result(tmp_path, reply=reply, summary=reply[:4000], response_id="saved-response")
    original = (tmp_path / "result.json").read_bytes()
    result = _run_step("Report result", tmp_path, env)
    assert result.returncode != 0
    assert (tmp_path / "result.json").read_bytes() == original
    report = (tmp_path / "report.md").read_text()
    assert env["EVIDENCE_URL"] in report
    assert hashlib.sha256(reply.encode()).hexdigest() in report
    assert f"{len(reply)} characters" in report
    assert "saved-response" in report
    assert "File status is not a review verdict" in report
    assert report in (tmp_path / "summary.md").read_text()


@pytest.mark.parametrize("reply,state", [(None, "unknown"), ("", "empty"), ("  \n", "available")])
def test_no_changes_without_substantive_reply_is_not_success(tmp_path, reply, state):
    env = _mock_tools(tmp_path)
    _result(tmp_path, reply=reply, reply_state=state, summary="")
    result = _run_step("Report result", tmp_path, env)
    assert result.returncode != 0
    assert f"Assistant reply: **{state}**" in (tmp_path / "comment.md").read_text()
    assert (tmp_path / "report-status.txt").read_text() == "failed"


def test_unconfirmed_artifact_cannot_report_delivery_success(tmp_path):
    env = _mock_tools(tmp_path)
    env.update(EVIDENCE_URL="", EVIDENCE_OUTCOME="failure")
    _result(tmp_path)
    result = _run_step("Report result", tmp_path, env)
    assert result.returncode != 0
    assert "Delivery is incomplete" in (tmp_path / "comment.md").read_text()


def test_interrupted_checkpoint_keeps_reply_metadata_and_reports_failure(tmp_path):
    import hashlib

    env = _mock_tools(tmp_path)
    reply = "Полученный до обрыва ответ. Ё🙂  \n"
    _result(tmp_path, status="running", reply=reply, summary=reply, response_id="saved-response")
    result = _run_step("Report result", tmp_path, env)
    assert result.returncode != 0
    report = (tmp_path / "comment.md").read_text()
    assert "Execution was interrupted" in report
    assert "**failed**" in report
    assert "saved-response" in report and reply in report
    assert hashlib.sha256(reply.encode()).hexdigest() in report
    assert env["EVIDENCE_URL"] in report
    assert json.loads((tmp_path / "result.json").read_text())["status"] == "running"
