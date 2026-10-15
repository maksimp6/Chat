"""Offline guards for the real suite; these results never count as real API PASS."""

import importlib.util
import json
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

SPEC = importlib.util.spec_from_file_location(
    "real_acceptance", Path(__file__).parent / "real_api/run.py"
)
live = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live)


def secret_file(tmp_path):
    directory = tmp_path / "secrets"
    directory.mkdir(mode=0o700)
    path = directory / "key.json"
    live.write_private(path, json.dumps({"api_key": "synthetic-canary-never-a-real-key"}).encode())
    return path


def test_no_opt_in_never_reads_secret_or_runs_application(monkeypatch, capsys):
    def forbidden(*args):
        raise AssertionError("secret/network access without opt-in")

    monkeypatch.setattr(live, "read_key", forbidden)
    monkeypatch.setattr(live, "real_acceptance", forbidden)
    assert (
        live.main(
            [
                "--secret-file",
                "unused",
                "--project-id",
                "folder",
                "--model",
                "flash",
                "--state-root",
                "unused",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED"


def test_private_key_read_and_permissions(tmp_path):
    path = secret_file(tmp_path)
    assert live.read_key(path) == "synthetic-canary-never-a-real-key"
    assert path.stat().st_mode & 0o777 == 0o600
    path.chmod(0o644)
    with pytest.raises(live.AcceptanceFailure, match="0600"):
        live.read_key(path)


def test_symlink_and_hardlink_keys_are_rejected(tmp_path):
    path = secret_file(tmp_path)
    link = path.with_name("link.json")
    link.symlink_to(path)
    with pytest.raises(OSError):
        live.read_key(link)
    link.unlink()
    os.link(path, link)
    with pytest.raises(live.AcceptanceFailure, match="0600"):
        live.read_key(path)


def test_private_state_rejects_readable_dir_and_symlink(tmp_path):
    directory = tmp_path / "public"
    directory.mkdir(mode=0o755)
    with pytest.raises(live.AcceptanceFailure, match="0700"):
        live.private_directory(directory)
    link = tmp_path / "alias"
    link.symlink_to(directory, target_is_directory=True)
    with pytest.raises(live.AcceptanceFailure, match="unsafe_directory"):
        live.private_directory(link)


def test_child_environment_excludes_provider_key_and_unrelated_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("ALICE_LAUNCH_SMOKE_YANDEX_API_KEY", "synthetic-canary-never-a-real-key")
    monkeypatch.setenv("GITHUB_TOKEN", "unrelated-test-token")
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.invalid")
    env = live.runtime_environment(tmp_path, 12345, "encryption-secret", "admin-secret")
    assert "YANDEX_API_KEY" not in env
    assert "ALICE_LAUNCH_SMOKE_YANDEX_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert "HTTPS_PROXY" not in env
    assert env["ALICE_PROVIDER_CREDENTIAL_KEY"] == "encryption-secret"
    assert env["ALICE_MEMORY_PATH"].endswith("alice.memory")


def test_import_does_not_follow_redirect_with_secret():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_PUT(self):
            requests.append(self.path)
            self.send_response(307)
            self.send_header("Location", "/stolen")
            self.end_headers()

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with live.LocalSession() as session:
            assert session.trust_env is False
            response = session.put(
                f"http://127.0.0.1:{server.server_port}/import",
                json={"api_key": "canary"},
                timeout=2,
            )
        assert response.status_code == 307
        assert requests == ["/import"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_status_or_http200_is_not_a_real_response():
    for payload in (
        {},
        {"status": "connected"},
        {"api_requests": [{}], "responses": [{"raw": {"status": "completed"}}]},
    ):
        with pytest.raises(live.AcceptanceFailure):
            live.verify_provider_response(payload)


def test_extra_requests_or_tools_fail_acceptance():
    payload = {
        "api_requests": [{}],
        "responses": [
            {"raw": {"id": "resp_synthetic", "status": "completed", "output": ["hello"]}}
        ],
    }
    assert live.verify_provider_response(payload)["id"] == "resp_synthetic"
    payload["api_requests"].append({})
    with pytest.raises(live.AcceptanceFailure, match="one_model_request"):
        live.verify_provider_response(payload)
    payload["api_requests"].pop()
    payload["tool_calls"] = [{}]
    with pytest.raises(live.AcceptanceFailure, match="unexpected_tool_call"):
        live.verify_provider_response(payload)


def test_plaintext_secret_is_detected_across_read_chunk_boundary(tmp_path):
    secret = "synthetic-canary-never-a-real-key"
    (tmp_path / "journal").write_bytes(b"x" * 65530 + secret.encode())
    with pytest.raises(live.AcceptanceFailure, match="plaintext_key"):
        live.assert_no_plaintext(tmp_path, secret)


def test_failure_does_not_echo_exception_secret_and_saves_private_evidence(
    tmp_path, monkeypatch, capsys
):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)

    def failed(args, work, evidence):
        evidence["stage"] = "conversation_create"
        raise RuntimeError("synthetic-canary-never-a-real-key")

    monkeypatch.setattr(live, "real_acceptance", failed)
    assert (
        live.main(
            [
                "--allow-paid-api",
                "--secret-file",
                str(tmp_path / "unused"),
                "--project-id",
                "folder",
                "--model",
                "flash",
                "--state-root",
                str(state),
            ]
        )
        == 1
    )
    output = capsys.readouterr().out
    assert "synthetic-canary" not in output
    result = json.loads(output)
    assert result["status"] == "FAIL"
    assert result["stage"] == "conversation_create"
    path = next(state.glob("*/result.json"))
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert json.loads(path.read_text())["status"] == "FAIL"


def test_installed_connected_with_broken_resolver_cannot_pass(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    calls = []

    def request(http, method, url, path, **kwargs):
        calls.append(path)
        if path == "/api/provider-credentials/status":
            return 200, {
                "providers": [
                    {"provider": "yandex", "status": "connected", "authorization_ok": True}
                ]
            }
        if path == "/api/sessions":
            return 201, {"id": "session"}
        if path == "/api/conversations":
            raise live.smoke.SmokeFailure("HTTP 502 synthetic-secret-not-for-output")
        return 200, {}

    monkeypatch.setattr(live.smoke, "_request_json", request)
    monkeypatch.setattr(live.smoke, "_git_output", lambda *args: "runner-sha")
    monkeypatch.setattr(
        live, "read_key", lambda path: pytest.fail("installed probe must not read/import a key")
    )
    code = live.main(
        ["--allow-paid-api", "--probe-installed", "--model", "flash", "--state-root", str(state)]
    )
    result = json.loads(capsys.readouterr().out)
    assert code == 1
    assert result["status"] == "FAIL"
    assert result["stage"] == "conversation_create"
    assert result["checks"] == {"provider_configured": True}
    assert result["test_type"] == "real_api_installed_backend"
    assert "/api/chat" not in calls
