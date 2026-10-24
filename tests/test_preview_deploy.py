import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "preview" / "environment_lifecycle.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "preview-deploy.yml"


def test_environment_lifecycle_script_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_preview_workflow_uses_host_environment_api_only():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "environment_lifecycle.sh" in workflow
    assert "if: vars.ALICE_HOST_PREVIEW_ENABLED == 'true'" in workflow
    assert "PREVIEW_COMMIT: ${{ github.event.pull_request.head.sha || github.sha }}" in workflow
    assert "ALICE_ENVIRONMENT_HOST_URL" in workflow
    for obsolete_transport in (
        "docker",
        "scp ",
        "ssh ",
        "server.sh",
        "runtime_port",
        "127.0.0.1",
    ):
        assert obsolete_transport not in workflow.lower()


def test_lifecycle_uses_exact_commit_gateway_and_cleanup(tmp_path):
    calls = tmp_path / "calls"
    fake_curl = tmp_path / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$CALLS"
case "$*" in
  *'/api/environments/env-test/start'*)
    printf '{"status":"RUNNING","runtime_pid":null,"runtime_port":null}' ;;
  *'/environments/env-test/healthz'*) printf 'ok' ;;
  *'/api/environments/env-test'*) printf '{}' ;;
  *'/api/environments '*|*'/api/environments')
    printf '{"environment_id":"env-test","commit_sha":"%s"}' "$PREVIEW_COMMIT" ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    commit = "a" * 40
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "CALLS": str(calls),
        "ALICE_ENVIRONMENT_HOST_URL": "https://preview.invalid/token",
        "PREVIEW_BRANCH": "feature/runtime",
        "PREVIEW_COMMIT": commit,
    }

    result = subprocess.run(
        [str(SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    requests = calls.read_text(encoding="utf-8")
    assert f'"commit_sha":"{commit}"' in requests
    assert "POST https://preview.invalid/token/api/environments/env-test/start" in requests
    assert "https://preview.invalid/token/environments/env-test/healthz" in requests
    assert "DELETE https://preview.invalid/token/api/environments/env-test" in requests
    assert "127.0.0.1" not in requests
